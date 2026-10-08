import pathlib
if not __debug__ and __name__ == '__main__':
    exec(compile(pathlib.Path(__file__).read_text(encoding='utf-8'),__file__,'exec',optimize=0),globals())
    raise SystemExit(0)
import copy,json,os,shlex,shutil,sqlite3,subprocess,sys,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT))
from tests import snapshot,make_request,receipt,mount_fixture
from plugin.protocol import canonical,loads,validate,seal,digest
from plugin.planner import intent,move,plan,request
from plugin.ledger import validate_receipt
from plugin.probe import prepare_probe

CLI=os.environ.get('KC_SQLITE_CLI','D:/software/KC-development/sqlite-3.26.0/tools/sqlite-tools-win32-x86-3260000/sqlite3.exe')
NODE=os.environ.get('KC_NODE','D:/software/node-v24.18.1-win-x64/node.exe')
BASH=os.environ.get('KC_BASH','C:/Program Files/Git/bin/bash.exe')

class Fixture:
    def __init__(self,s=None,options=None):
        self.temp=tempfile.TemporaryDirectory(prefix='.exec-',dir=ROOT)
        self.root=Path(self.temp.name); self.mount=self.root/'mount'; self.mount.mkdir()
        self.s=copy.deepcopy(s or snapshot())
        (self.root/'fixture.marker').write_text('KC isolated executor fixture')
        (self.root/'options.json').write_bytes(canonical(options or {}))
        (self.root/'session_token').write_text('fixture-token')
        (self.mount/'documents').mkdir()
        for b in self.s['books']:
            p=self.mount/'documents'/(b['uuid']+'.txt');p.write_text('fixture book, never modified')
            b['location']=p.as_posix()
        mount_fixture(self.mount,self.s)
        caps=dict(self.s['capabilities'],device=self.s['device'],firmware='unknown')
        (self.mount/'kc-sync/state/capabilities.json').write_bytes(canonical(caps))
        (self.mount/'kc-sync/state/policy.json').write_bytes(canonical(self.s['policy']))
        (self.mount/'metadata.calibre').write_bytes(canonical([dict(lpath='documents/'+b['uuid']+'.txt',uuid=b['calibre_uuid']) for b in self.s['books'] if b['calibre_uuid']]))
        db=sqlite3.connect(self.root/'cc.db')
        db.executescript('''CREATE TABLE Entries(p_uuid TEXT PRIMARY KEY,p_type TEXT,p_titles_0_nominal TEXT,
          p_location TEXT,p_cdeKey TEXT,p_cdeType TEXT,p_collectionCount INTEGER);
          CREATE TABLE Collections(i_collection_uuid TEXT,i_member_uuid TEXT,i_member_cde_type TEXT,
          i_member_cde_key TEXT,i_member_is_present INTEGER,i_is_sideloaded INTEGER,i_order INTEGER);
          CREATE UNIQUE INDEX members ON Collections(i_collection_uuid,i_member_uuid);''')
        for b in self.s['books']:
            db.execute('INSERT INTO Entries VALUES(?,?,?,?,?,?,?)',(b['uuid'],'Entry:Item',b['title'],b['location'],b['cde_key'],b['cde_type'],b['collection_count']))
        for c in self.s['collections']: db.execute('INSERT INTO Entries(p_uuid,p_type,p_titles_0_nominal) VALUES(?,?,?)',(c['uuid'],'Collection',c['name']))
        for r in self.s['relations']: db.execute('INSERT INTO Collections VALUES(?,?,?,?,?,?,?)',(r['collection_uuid'],r['book_uuid'],r['member_type'],r['member_key'],r['member_present'],r['sideloaded'],r['order']))
        db.commit();db.close()
        self.bin=self.root/'bin';self.bin.mkdir();self.device=self.root/'device';shutil.copytree(ROOT/'device',self.device)
        # Same standalone checker source + SQLite amalgamation, host executable.
        host_check=self.device/'kc-backup-check-test.exe'
        shutil.copyfile(ROOT/'native/kc-backup-check-test.exe',host_check)
        (self.device/'kc-backup-check').write_text('#!/bin/sh\nprintf "check\\n" >> '+shlex.quote((self.root/'checker-calls.txt').as_posix())+'\nexec '+shlex.quote(host_check.as_posix())+' "$@"\n',encoding='utf8',newline='\n')
        export=self.device/'export.sql'
        export.write_text(export.read_text(encoding='utf8').replace("'/mnt/us/'",repr(self.mount.as_posix()+'/')),encoding='utf8',newline='\n')
        for path in self.device.glob('*.sh'):
            body=path.read_text(encoding='utf-8').replace("DB='/var/local/cc.db'","DB='"+(self.root/'cc.db').as_posix()+"'")
            body=body.replace("MOUNT='/mnt/us'","MOUNT='"+self.mount.as_posix()+"'")
            body=body.replace("LOCK='/tmp/kc-sync.lock'","LOCK='"+(self.root/'lock').as_posix()+"'")
            body=body.replace("TOKEN='/tmp/session_token'","TOKEN='"+(self.root/'session_token').as_posix()+"'")
            body=body.replace('$DIR/kc-backup-check',(self.device/'kc-backup-check').as_posix())
            path.write_text(body,encoding='utf-8',newline='\n')
        (self.bin/'sqlite3').write_text('#!/bin/sh\nexec '+shlex.quote(NODE)+' '+shlex.quote((ROOT/'sqlite_posix_adapter.mjs').as_posix())+' "$@"\n',encoding='utf-8',newline='\n')
        for name,kind in (('curl','curl'),('uuidgen','uuid'),('sleep','sleep'),('sync','sync')):
            (self.bin/name).write_text('#!/bin/sh\nexec '+shlex.quote(NODE)+' '+shlex.quote((ROOT/'executor_driver.mjs').as_posix())+' '+kind+' "$@"\n',encoding='utf-8',newline='\n')
    def close(self): self.temp.cleanup()
    def send(self,ops):
        p=plan(self.s,ops,[c['uuid'] for c in self.s['collections']],'library')
        req=request(p,self.s,p.data['inputs_digest'])
        return self.write(req)
    def write(self,req):
        (self.mount/'kc-sync/inbox'/(req['job_id']+'.json')).write_bytes(canonical(req)); return req
    def run(self,args=()):
        path=self.bin.as_posix();path='/'+path[0].lower()+path[2:]
        cmd='PATH='+shlex.quote(path)+':/usr/bin:/bin; export PATH; exec sh '+shlex.quote((self.device/'run.sh').as_posix())
        cmd+=' '+' '.join(map(shlex.quote,args))
        return subprocess.run([BASH,'--noprofile','--norc','-c',cmd],env=dict(os.environ,KC_EXECUTOR_FIXTURE=str(self.root)),capture_output=True,timeout=180)
    def query(self,sql):
        db=sqlite3.connect(self.root/'cc.db');rows=db.execute(sql).fetchall();db.close();return rows
    def result(self,req):
        result=validate(loads((self.mount/'kc-sync/results'/(req['job_id']+'.json')).read_bytes()))
        validate_receipt(req,result);return result
    def posts(self):
        p=self.root/'trace.jsonl'
        return [json.loads(l) for l in p.read_text().splitlines() if json.loads(l)['kind']=='curl'] if p.exists() else []
    def logs(self):
        return '\n'.join(p.read_text(encoding='utf8') for p in (self.mount/'kc-sync/logs').glob('KC运行结果-*.txt'))
    def checks(self):
        path=self.root/'checker-calls.txt'
        return len(path.read_text().splitlines()) if path.exists() else 0

class Executor(unittest.TestCase):
    def setUp(self): self.f=Fixture()
    def tearDown(self): self.f.close()
    def success(self,r): self.assertEqual(r.returncode,0,(r.stdout.decode(),r.stderr.decode()))
    def test_create_empty_rename_delete_and_book_preservation(self):
        req=self.f.send([intent('rename_collection','c1',dict(name='新名字')),
            intent('delete_collection','c2'),intent('create_collection','new',dict(name='新空架'))])
        self.success(self.f.run());result=self.f.result(req)
        self.assertTrue(all(o['status']=='confirmed' for o in result['operations']))
        self.assertEqual(self.f.query("SELECT p_titles_0_nominal FROM Entries WHERE p_uuid='c1'"),[('新名字',)])
        self.assertEqual(self.f.query("SELECT count(*) FROM Entries WHERE p_type='Entry:Item'"),[(3,)])
        self.assertEqual(len(list((self.f.mount/'kc-sync/state/backups').glob('*.db'))),1)
        self.assertEqual(self.f.checks(),1) # Multiple operations share one backup validation.
        count=len(self.f.posts());self.success(self.f.run());self.assertEqual(len(self.f.posts()),count)
    def test_move_multimembership_and_zero_count(self):
        ops=move(['b0'],'c1','c2')+[intent('remove_members','c1',dict(members=['b1']))]
        req=self.f.send(ops);self.success(self.f.run());self.f.result(req)
        self.assertEqual(self.f.query('SELECT i_collection_uuid,i_member_uuid FROM Collections ORDER BY 1,2'),[('c2','b0')])
        self.assertEqual(self.f.query("SELECT p_uuid,p_collectionCount FROM Entries WHERE p_type='Entry:Item' ORDER BY p_uuid"),[('b0',1),('b1',0),('b2',0)])
    def test_noop_has_no_backup_or_post(self):
        req=self.f.send([intent('verify_state','c1')]);self.success(self.f.run());self.f.result(req)
        self.assertEqual(self.f.posts(),[])
        self.assertEqual(list((self.f.mount/'kc-sync/state/backups').glob('*.db')),[])
        self.assertEqual(self.f.checks(),0)
    def test_partial_native_update_stays_pending_and_does_not_repost(self):
        (self.f.root/'options.json').write_bytes(canonical(dict(omit_counts=True)))
        req=self.f.send([intent('remove_members','c1',dict(members=['b0']))])
        self.assertEqual(self.f.run().returncode,4)
        self.assertEqual(self.f.result(req)['operations'][0]['status'],'pending')
        count=len(self.f.posts());self.assertEqual(self.f.run().returncode,4);self.assertEqual(len(self.f.posts()),count)
        db=sqlite3.connect(self.f.root/'cc.db');db.execute("UPDATE Entries SET p_collectionCount=0 WHERE p_uuid='b0'");db.commit();db.close()
        self.success(self.f.run());self.assertEqual(len(self.f.posts()),count)
        self.assertEqual(self.f.result(req)['operations'][0]['status'],'confirmed')
    def test_timeout_applied_is_verified_without_retry(self):
        (self.f.root/'options.json').write_bytes(canonical(dict(timeout_applied=True)))
        req=self.f.send([intent('add_members','c2',dict(members=['b0']))]);self.success(self.f.run())
        self.assertEqual(len(self.f.posts()),1);self.assertEqual(self.f.result(req)['operations'][0]['status'],'confirmed')
    def test_stale_member_state_rejected_before_backup(self):
        req=self.f.send([intent('remove_members','c1',dict(members=['b0']))])
        db=sqlite3.connect(self.f.root/'cc.db');db.execute("UPDATE Entries SET p_titles_0_nominal='Changed externally' WHERE p_uuid='c1'");db.commit();db.close()
        self.success(self.f.run());self.assertEqual(self.f.posts(),[])
        self.assertEqual(self.f.result(req)['operations'][0]['status'],'conflict')
    def test_corrupt_request_fails_closed(self):
        req=self.f.send([intent('delete_collection','c1')]);path=self.f.mount/'kc-sync/inbox'/(req['job_id']+'.json')
        path.write_bytes(path.read_bytes().replace(b'delete_collection',b'rename_collection'))
        self.assertNotEqual(self.f.run().returncode,0);self.assertEqual(self.f.posts(),[])
    def test_explicit_probe_enables_capabilities_only_after_all_six_checks(self):
        req=prepare_probe(self.f.s,'b2','library');self.f.write(req)
        (self.f.mount/'kc-sync/state/probe.json').write_bytes(canonical(dict(job_id=req['job_id'])))
        self.success(self.f.run(['--self-test']));result=self.f.result(req)
        self.assertEqual([o['status'] for o in result['operations']],['confirmed']*6)
        self.assertEqual(self.f.query("SELECT p_collectionCount FROM Entries WHERE p_uuid='b2'"),[(0,)])
        self.assertEqual(self.f.query("SELECT count(*) FROM Entries WHERE p_titles_0_nominal LIKE 'KC-PROBE-%'"),[(0,)])
        caps=loads((self.f.mount/'kc-sync/state/capabilities.json').read_bytes())
        self.assertIn(req['job_id'],caps['evidence'])
        self.assertIn('编辑能力验证成功：六步全部通过。',self.f.logs())
    def test_policy_separate_no_native_post_or_backup(self):
        from plugin.policy import build,send
        from plugin.transport import DeviceStore
        task=build(self.f.s,['c1']);send(DeviceStore(self.f.mount),task)
        self.success(self.f.run())
        policy=loads((self.f.mount/'kc-sync/state/policy.json').read_bytes())
        self.assertEqual(policy['protected_collections'],['c1']);self.assertEqual(policy['version'],2)
        self.assertEqual(self.f.posts(),[]);self.assertEqual(list((self.f.mount/'kc-sync/state/backups').glob('*.db')),[])
    def test_timeout_unapplied_remains_pending_no_repost(self):
        (self.f.root/'options.json').write_bytes(canonical(dict(no_apply_timeout=True)))
        req=self.f.send([intent('add_members','c2',dict(members=['b0']))])
        self.assertEqual(self.f.run().returncode,4);self.assertEqual(len(self.f.posts()),1)
        self.assertEqual(self.f.run().returncode,4);self.assertEqual(len(self.f.posts()),1)
        self.assertEqual(self.f.result(req)['operations'][0]['status'],'pending')
    def test_protected_and_corrupt_pending_stop_without_post(self):
        req=self.f.send([intent('delete_collection','c1')])
        policy=dict(self.f.s['policy'],protected_collections=['c1'])
        (self.f.mount/'kc-sync/state/policy.json').write_bytes(canonical(policy))
        self.success(self.f.run());self.assertEqual(self.f.posts(),[])
        self.assertEqual(self.f.result(req)['operations'][0]['status'],'conflict')
        (self.f.mount/'kc-sync/state/pending.json').write_bytes(canonical(dict(job_id=req['job_id'],journal_digest='0'*64)))
        self.assertEqual(self.f.run().returncode,4);self.assertEqual(self.f.posts(),[])
    def test_probe_entry_without_explicit_task_is_readonly(self):
        self.assertEqual(self.f.run(['--self-test']).returncode,4)
        self.assertEqual(self.f.posts(),[])
    def test_backup_failure_stops_before_native_post(self):
        self.f.send([intent('add_members','c2',dict(members=['b0']))])
        wrapper=self.f.bin/'sqlite3';body=wrapper.read_text(encoding='utf8')
        body=body.replace('#!/bin/sh\n','#!/bin/sh\nfor arg in "$@"; do case "$arg" in ".backup "*) exit 12 ;; esac; done\n')
        wrapper.write_text(body,encoding='utf8',newline='\n')
        self.assertNotEqual(self.f.run().returncode,0);self.assertEqual(self.f.posts(),[])
        self.assertEqual(self.f.query("SELECT count(*) FROM Collections WHERE i_collection_uuid='c2'"),[(0,)])

    def test_changed_book_size_stays_pending_and_does_not_repost(self):
        (self.f.root/'options.json').write_bytes(canonical(dict(resize_book='b0')))
        req=self.f.send([intent('add_members','c2',dict(members=['b0']))])
        self.assertEqual(self.f.run().returncode,4)
        self.assertEqual(self.f.result(req)['operations'][0]['status'],'pending')
        self.assertEqual(self.f.run().returncode,4)
        self.assertEqual(len(self.f.posts()),1)

    def test_icu_backup_and_verified_partial_recovery_before_post(self):
        db=sqlite3.connect(self.f.root/'cc.db')
        db.create_collation('icu',lambda a,b:(b>a)-(b<a))
        db.executescript('ALTER TABLE Entries ADD COLUMN title_sort TEXT COLLATE icu; CREATE INDEX title_sort_idx ON Entries(title_sort);')
        db.execute("UPDATE Entries SET title_sort=p_uuid");db.commit();db.close()
        folder=self.f.mount/'kc-sync/state/backups';folder.mkdir()
        previous=folder/'cc-20000101-000000-1.db.partial';shutil.copyfile(self.f.root/'cc.db',previous)
        original=previous.read_bytes()
        req=self.f.send([intent('add_members','c2',dict(members=['b0']))])
        self.success(self.f.run());self.assertEqual(self.f.result(req)['operations'][0]['status'],'confirmed')
        self.assertEqual(len(self.f.posts()),1)
        self.assertFalse(previous.exists())
        self.assertEqual(previous.with_suffix('').read_bytes(),original)
        self.assertEqual(len(list(folder.glob('*.db'))),2)
        self.assertEqual(self.f.checks(),2) # One old partial plus one newly created backup.

    def test_bad_partial_backup_blocks_all_posts(self):
        folder=self.f.mount/'kc-sync/state/backups';folder.mkdir()
        previous=folder/'cc-20000101-000000-1.db.partial';previous.write_bytes(b'not a database')
        self.f.send([intent('add_members','c2',dict(members=['b0']))])
        self.assertEqual(self.f.run().returncode,4);self.assertEqual(self.f.posts(),[])
        self.assertEqual(previous.read_bytes(),b'not a database')
        self.assertIn('EXIT_CODE=4',self.f.logs())
        self.assertIn('LAST_PHASE=recover_backup_partials',self.f.logs())
        self.assertNotIn('fixture-token',self.f.logs())

    def test_missing_icu_component_blocks_all_posts(self):
        db=sqlite3.connect(self.f.root/'cc.db');db.create_collation('icu',lambda a,b:(a>b)-(a<b))
        db.executescript('ALTER TABLE Entries ADD COLUMN title_sort TEXT COLLATE icu; CREATE INDEX title_sort_idx ON Entries(title_sort);')
        db.close()
        (self.f.device/'kc-backup-check').unlink()
        self.f.send([intent('add_members','c2',dict(members=['b0']))])
        self.assertEqual(self.f.run().returncode,4);self.assertEqual(self.f.posts(),[])

    def test_lock_failure_is_logged_and_foreign_lock_preserved(self):
        lock=self.f.root/'lock';lock.mkdir();(lock/'pid').write_text('foreign-lock')
        self.assertEqual(self.f.run().returncode,3)
        self.assertEqual((lock/'pid').read_text(),'foreign-lock')
        self.assertIn('LAST_PHASE=acquire_lock',self.f.logs())
        self.assertIn('RUN_FINISHED=1',self.f.logs())
        self.assertEqual(self.f.checks(),0);self.assertEqual(self.f.posts(),[])

    def test_no_load_command_needed_for_icu_backup(self):
        wrapper=self.f.bin/'sqlite3';body=wrapper.read_text()
        wrapper.write_text(body.replace('#!/bin/sh\n','#!/bin/sh\nfor arg do case "$arg" in .load*) echo "unknown command: load" >&2; exit 2 ;; esac; done\n'),newline='\n')
        db=sqlite3.connect(self.f.root/'cc.db');db.create_collation('icu',lambda a,b:(a>b)-(a<b))
        db.execute('ALTER TABLE Entries ADD COLUMN title_sort TEXT COLLATE icu');db.close()
        req=self.f.send([intent('add_members','c2',dict(members=['b0']))])
        self.success(self.f.run());self.assertEqual(self.f.result(req)['operations'][0]['status'],'confirmed')
        self.assertEqual(self.f.checks(),1);self.assertEqual(len(self.f.posts()),1)

if __name__=='__main__':
    start=time.perf_counter();result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Executor))
    (ROOT/'executor-results.json').write_text(json.dumps(dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
        seconds=time.perf_counter()-start,boundary='Actual shell + SQLite 3.26, simulated /change. NOT firmware certification'),indent=2),encoding='utf-8')
    if not result.wasSuccessful(): raise SystemExit(1)
