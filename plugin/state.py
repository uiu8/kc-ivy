"""kc-ivy private transactional journal. Never opens a Calibre/Kindle database."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .protocol import canonical, loads, digest, Invalid


class StateStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'kc-plus-state.sqlite'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS profiles(k TEXT PRIMARY KEY,rev INTEGER NOT NULL,body BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,profile TEXT NOT NULL,body BLOB NOT NULL,status TEXT NOT NULL DEFAULT 'staged');
                CREATE TABLE IF NOT EXISTS receipts(job TEXT PRIMARY KEY,body BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS applied(job TEXT,op TEXT,PRIMARY KEY(job,op));
                CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY,profile TEXT,body BLOB);
                CREATE TABLE IF NOT EXISTS column_jobs(job TEXT,digest TEXT,body BLOB,done INTEGER DEFAULT 0,PRIMARY KEY(job,digest));
                CREATE TABLE IF NOT EXISTS column_applied(job TEXT,op TEXT,PRIMARY KEY(job,op));
                CREATE TABLE IF NOT EXISTS baseline_applied(job TEXT,op TEXT,PRIMARY KEY(job,op));
                CREATE TABLE IF NOT EXISTS drafts(profile TEXT PRIMARY KEY,body BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY,kind TEXT NOT NULL,profile TEXT NOT NULL,rev INTEGER NOT NULL,body BLOB NOT NULL);
            ''')
            if 'status' not in {r[1] for r in db.execute('PRAGMA table_info(jobs)')}:
                db.execute("ALTER TABLE jobs ADD COLUMN status TEXT NOT NULL DEFAULT 'staged'")
                for jid,body in db.execute('SELECT id,body FROM jobs').fetchall():
                    db.execute('UPDATE jobs SET status=? WHERE id=?',(loads(body).get('status','staged'),jid))
            db.execute('CREATE INDEX IF NOT EXISTS job_status ON jobs(profile,status)')
    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=5)
        db.execute('PRAGMA synchronous=FULL')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback(); raise
        finally: db.close()
    @staticmethod
    def key(library, device): return digest([library, device])
    def workspace(self, ident):
        with self.connect() as db:
            row=db.execute('SELECT body FROM workspaces WHERE id=?',(ident,)).fetchone()
            return loads(row[0]) if row else None
    def workspaces(self,kind,profile):
        with self.connect() as db:
            return [loads(r[0]) for r in db.execute('SELECT body FROM workspaces WHERE kind=? AND profile=? ORDER BY rowid DESC',(kind,profile))]
    def save_workspace(self,value,expected):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT rev FROM workspaces WHERE id=?',(value['id'],)).fetchone()
            if (row[0] if row else 0)!=expected:raise Invalid('备份/迁移记录已改变，请重新打开')
            value=dict(value,revision=expected+1)
            db.execute('INSERT OR REPLACE INTO workspaces VALUES(?,?,?,?,?)',(value['id'],value['kind'],value['profile'],value['revision'],canonical(value)))
            return value
    def get(self, key):
        with self.connect() as db:
            row = db.execute('SELECT body FROM profiles WHERE k=?', (key,)).fetchone()
            return loads(row[0]) if row else None
    def revision(self,key):
        with self.connect() as db:
            row=db.execute('SELECT rev FROM profiles WHERE k=?',(key,)).fetchone()
            return row[0] if row else 0
    def receipt_known(self,result):
        with self.connect() as db:
            row=db.execute('SELECT body FROM receipts WHERE job=?',(result['job_id'],)).fetchone()
            return bool(row and loads(row[0])['result_digest']==result['result_digest'])
    def save(self, key, value, expected_revision):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT rev,body FROM profiles WHERE k=?',(key,)).fetchone()
            if (row[0] if row else 0) != expected_revision:
                raise Invalid('配置或采用范围已改变，请重新读取')
            if row: db.execute('INSERT INTO history(profile,body) VALUES(?,?)',(key,row[1]))
            db.execute('DELETE FROM history WHERE profile=? AND id NOT IN(SELECT id FROM history WHERE profile=? ORDER BY id DESC LIMIT 20)',(key,key))
            value = dict(value, revision=expected_revision+1)
            db.execute('INSERT OR REPLACE INTO profiles VALUES(?,?,?)',(key,value['revision'],canonical(value)))
            return value
    def reconcile_pending(self, key, snapshot, descriptors):
        """Migrate legacy library-scoped aliases using freshly verified file hashes.

        Signed requests/receipts stay immutable. Only the local profile and
        unsent draft are rebound, with the prior profile retained in history.
        """
        from .deferred import previous_aliases, remap_profile
        books={b['uuid']:b for b in snapshot['books']}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT rev,body FROM profiles WHERE k=?',(key,)).fetchone()
            if not row:return False
            profile=loads(row[1]);mapping={}
            for item in descriptors:
                book=books.get(item['alias'])
                if not book:continue
                for old in previous_aliases(snapshot,book,item,profile['library_uuid']):
                    if old!=item['alias']:mapping[old]=item['alias']
            updated=remap_profile(profile,[dict(alias=a,uuid=b) for a,b in mapping.items()])
            draft_row=db.execute('SELECT body FROM drafts WHERE profile=?',(key,)).fetchone()
            draft=loads(draft_row[0]) if draft_row else None
            draft_changed=False
            if draft:
                for op in draft.get('intents',[]):
                    members=op.get('args',{}).get('members')
                    if members is not None:
                        new=[mapping.get(b,b) for b in members]
                        draft_changed=draft_changed or new!=members
                        op['args']['members']=new
            changed=updated!=profile
            if changed:
                db.execute('INSERT INTO history(profile,body) VALUES(?,?)',(key,row[1]))
                updated['revision']=row[0]+1
                db.execute('UPDATE profiles SET rev=?,body=? WHERE k=?',(updated['revision'],canonical(updated),key))
            if draft_changed:
                draft['resolutions']={};draft['issues']=[]
                db.execute('UPDATE drafts SET body=? WHERE profile=?',(canonical(draft),key))
            return changed or draft_changed

    def stage(self, key, job):
        request = job['request']
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT body FROM jobs WHERE id=?',(request['job_id'],)).fetchone()
            if previous:
                if loads(previous[0]) != job: raise Invalid('相同本地任务 ID 内容不同')
                return
            if db.execute("SELECT 1 FROM jobs WHERE profile=? AND status NOT IN('complete','closed') LIMIT 1",(key,)).fetchone():
                raise Invalid('此设备仍有待发送或待确认任务，请先处理原任务')
            if job.get('migration_id'):
                if db.execute('SELECT 1 FROM drafts WHERE profile=?',(key,)).fetchone():raise Invalid('请先处理普通草稿，再发送迁移任务')
                row=db.execute('SELECT body FROM workspaces WHERE id=?',(job['migration_id'],)).fetchone()
                session=loads(row[0]) if row else None
                if not session or session['revision']!=job['migration_revision']:raise Invalid('迁移选择已变化，请重新预览')
                session.update(active_job=request['job_id'],revision=session['revision']+1)
                db.execute('UPDATE workspaces SET rev=?,body=? WHERE id=?',(session['revision'],canonical(session),session['id']))
            db.execute('INSERT INTO jobs(id,profile,body,status) VALUES(?,?,?,?)',(request['job_id'],key,canonical(job),job['status']))
            db.execute('DELETE FROM drafts WHERE profile=?',(key,))
    def save_draft(self,key,value):
        with self.connect() as db:
            if value.get('intents'):db.execute('INSERT OR REPLACE INTO drafts VALUES(?,?)',(key,canonical(value)))
            else:db.execute('DELETE FROM drafts WHERE profile=?',(key,))
    def draft(self,key):
        with self.connect() as db:
            row=db.execute('SELECT body FROM drafts WHERE profile=?',(key,)).fetchone()
            return loads(row[0]) if row else None
    def drop_completed(self,jid,result_digest):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT body,status FROM jobs WHERE id=?',(jid,)).fetchone()
            if not row or row[1]!='complete' or loads(row[0]).get('result',{}).get('result_digest')!=result_digest:
                raise Invalid('历史任务状态已变化，保留本地记录')
            for table in ('receipts','applied','column_jobs','column_applied','baseline_applied'):
                db.execute('DELETE FROM '+table+' WHERE job=?',(jid,))
            db.execute('DELETE FROM jobs WHERE id=?',(jid,))
    def jobs(self, key, status=None):
        with self.connect() as db:
            query='SELECT body FROM jobs WHERE profile=?'+(' AND status=?' if status else '')
            return [loads(r[0]) for r in db.execute(query,(key,status) if status else (key,))]
    def job_summaries(self,key):
        with self.connect() as db:
            return [dict(job_id=jid,status=status) for jid,status in db.execute('SELECT id,status FROM jobs WHERE profile=? ORDER BY rowid DESC',(key,))]
    def job(self, job_id):
        with self.connect() as db:
            row=db.execute('SELECT body FROM jobs WHERE id=?',(job_id,)).fetchone()
            return loads(row[0]) if row else None
    def close_staged(self,job_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT body FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not row:raise Invalid('任务不存在')
            job=loads(row[0])
            if job['status']!='staged':raise Invalid('只能取消确认未发布的本地任务')
            job.update(status='closed',cancel_reason='Closed after checking current device records; historical publication may be unknown')
            db.execute('UPDATE jobs SET body=?,status=? WHERE id=?',(canonical(job),'closed',job_id))
            if job.get('migration_id'):
                row=db.execute('SELECT body FROM workspaces WHERE id=?',(job['migration_id'],)).fetchone()
                if row:
                    s=loads(row[0]);s.update(active_job='',revision=s['revision']+1)
                    db.execute('UPDATE workspaces SET rev=?,body=? WHERE id=?',(s['revision'],canonical(s),s['id']))
    def record_transfer(self,job_id,phase,message=''):
        from datetime import datetime,timezone
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT body FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not row:raise Invalid('本地任务不存在')
            job=loads(row[0]);events=list(job.get('transfer_events',[]))
            events.append(dict(phase=phase,time=datetime.now(timezone.utc).isoformat(),message=str(message)))
            job['transfer_events']=events[-30:]
            if phase=='verified_on_device':job['publication_verified']=True
            db.execute('UPDATE jobs SET body=? WHERE id=?',(canonical(job),job_id))
    def commit_receipt(self, key, result, transform):
        """Commit each confirmed operation once with profile effects in one txn.

        transform returns a new profile and must not perform external writes.
        Calibre column writeback is a separately journaled step in workflow.
        """
        from .ledger import validate_receipt
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT body FROM jobs WHERE id=? AND profile=?',(result['job_id'],key)).fetchone()
            if not row: raise Invalid('回执没有对应的本地预览和提交记录')
            job=loads(row[0]); statuses=validate_receipt(job['request'],result)
            job['result']=result
            profile=loads(db.execute('SELECT body FROM profiles WHERE k=?',(key,)).fetchone()[0])
            done={r[0] for r in db.execute('SELECT op FROM applied WHERE job=?',(result['job_id'],))}
            if any(statuses[oid]['status']!='confirmed' for oid in done):
                raise Invalid('回执试图撤销已经确认的操作')
            confirmed=[o for o in job['request']['operations'] if statuses[o['op_id']]['status']=='confirmed' and o['op_id'] not in done]
            from .deferred import remap_profile
            remapped=remap_profile(profile,result.get('book_bindings',[]))
            if confirmed or remapped!=profile:
                db.execute('INSERT INTO history(profile,body) VALUES(?,?)',(key,canonical(profile)))
                db.execute('DELETE FROM history WHERE profile=? AND id NOT IN(SELECT id FROM history WHERE profile=? ORDER BY id DESC LIMIT 20)',(key,key))
                profile=transform(profile,job,confirmed) if confirmed else remapped
                profile['revision']+=1
                db.execute('UPDATE profiles SET rev=?,body=? WHERE k=?',(profile['revision'],canonical(profile),key))
                db.executemany('INSERT INTO applied VALUES(?,?)',[(result['job_id'],o['op_id']) for o in confirmed])
            job['status']='complete' if all(v['status']=='confirmed' for v in statuses.values()) else 'pending' if any(v['status']=='pending' for v in statuses.values()) else 'closed'
            job['result']=result
            if job.get('migration_id'):
                from .migration import fold_receipt
                row=db.execute('SELECT body FROM workspaces WHERE id=?',(job['migration_id'],)).fetchone()
                if not row:raise Invalid('迁移记录缺失，不能丢失执行进度')
                session=fold_receipt(loads(row[0]),job,confirmed)
                session['revision']+=1
                db.execute('UPDATE workspaces SET rev=?,body=? WHERE id=?',(session['revision'],canonical(session),session['id']))
                job['migration_folded']=True
            db.execute('UPDATE jobs SET body=?,status=? WHERE id=?',(canonical(job),job['status'],result['job_id']))
            db.execute('INSERT OR REPLACE INTO receipts VALUES(?,?)',(result['job_id'],canonical(result)))
            return profile,job,confirmed

    def column_pending(self,job):
        with self.connect() as db:
            row=db.execute('SELECT body FROM column_jobs WHERE job=? AND done=0',(job,)).fetchone()
            return loads(row[0]) if row else None
    def column_done(self,job):
        with self.connect() as db:return {r[0] for r in db.execute('SELECT op FROM column_applied WHERE job=?',(job,))}
    def baseline_done(self,job):
        with self.connect() as db:return {r[0] for r in db.execute('SELECT op FROM baseline_applied WHERE job=?',(job,))}
    def accept_column_baseline(self,key,profile,job,operations):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT rev,body FROM profiles WHERE k=?',(key,)).fetchone()
            if not row or row[0]!=profile['revision']:raise Invalid('回填记录已改变，请重新读取')
            db.execute('INSERT INTO history(profile,body) VALUES(?,?)',(key,row[1]))
            value=dict(profile,revision=profile['revision']+1)
            db.execute('UPDATE profiles SET rev=?,body=? WHERE k=?',(value['revision'],canonical(value),key))
            db.executemany('INSERT OR IGNORE INTO baseline_applied VALUES(?,?)',[(job,op) for op in operations])
            return value
    def stage_column(self,value):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM column_jobs WHERE job=? AND done=0',(value['job_id'],)).fetchone():raise Invalid('已有回填等待恢复')
            db.execute('INSERT INTO column_jobs(job,digest,body) VALUES(?,?,?)',(value['job_id'],value['result_digest'],canonical(value)))
    def finish_column(self,value):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.executemany('INSERT OR IGNORE INTO column_applied VALUES(?,?)',[(value['job_id'],oid) for oid in value['operations']])
            db.execute('UPDATE column_jobs SET done=1 WHERE job=? AND digest=?',(value['job_id'],value['result_digest']))
