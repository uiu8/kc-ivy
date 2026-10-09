"""Portable shelf data and additive, target-bound migration sessions."""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime,timezone
from uuid import uuid4
from .protocol import Invalid,canonical,digest,checkpoint
from .planner import Catalog,intent,request
from .metadata import read_metadata,assert_library
from .workflow import prepare

SCHEMA='kc-bookshelf-backup/v1'
def now():return datetime.now(timezone.utc).isoformat()
def seal_backup(value):
    value={k:v for k,v in value.items() if k!='digest'}
    return dict(value,digest=digest(value))

def validate_backup(value):
    if not isinstance(value,dict) or value.get('schema')!=SCHEMA:raise Invalid('不是受支持的书架备份')
    if value.get('digest')!=digest({k:v for k,v in value.items() if k!='digest'}):raise Invalid('书架备份摘要不符')
    if not isinstance(value.get('source'),dict):raise Invalid('缺少备份来源')
    for key in ('collections','books','relations','unresolved_relations'):
        if not isinstance(value.get(key),list):raise Invalid('备份缺少 '+key)
    cs={};bs={}
    for items,out,name in ((value['collections'],cs,'收藏夹'),(value['books'],bs,'书籍')):
        for item in items:
            if not isinstance(item,dict) or not isinstance(item.get('id'),str) or not item['id'] or item['id'] in out:raise Invalid(name+'编号重复或无效')
            out[item['id']]=item
    for c in cs.values():
        if not isinstance(c.get('name'),str) or not c['name'].strip():raise Invalid('收藏夹名称无效')
    for b in bs.values():
        if not isinstance(b.get('title'),str) or not isinstance(b.get('calibre_uuid'),(str,type(None))):raise Invalid('书籍资料无效')
    for edge in value['relations']:
        if not isinstance(edge,list) or len(edge)!=2 or edge[0] not in cs or edge[1] not in bs:raise Invalid('备份关系引用不存在')
    for row in value['unresolved_relations']:
        if not isinstance(row,dict) or row.get('collection') not in cs:raise Invalid('未知关系引用不存在')
    return value

def export_snapshot(snapshot,selected=None,selected_books=None,selected_relations=None):
    cat=Catalog(snapshot);selected=set(cat.collections if selected is None else selected)
    if selected_books is not None:
        selected_books=set(selected_books)
        selected={cid for cid in selected if cat.members[cid]&selected_books}
    cs={c:str(uuid4()) for c in cat.collections if c in selected};bs={}
    books=[];relations=[];unknown=[]
    for cid in cs:
        for bid in sorted(cat.members[cid],key=str):
            book=cat.books.get(bid)
            if not book or (book.get('location') or '').lower().endswith('.sh'):
                unknown.append(dict(collection=cs[cid],reason='脚本或未识别成员，不作为书籍迁移'));continue
            if selected_books is not None and bid not in selected_books:continue
            if selected_relations is not None and bid not in selected_relations.get(cid,()):continue
            if bid not in bs:
                bs[bid]=str(uuid4());books.append(dict(id=bs[bid],title=book['title'] or '',
                    calibre_uuid=book['calibre_uuid'] if snapshot['mapping']['stable'] and book['metadata_matches']==1 else None))
            relations.append([cs[cid],bs[bid]])
        if not cat.collections[cid]['complete'] and not any(r['collection']==cs[cid] for r in unknown):
            unknown.append(dict(collection=cs[cid],reason='原收藏夹关系不完整'))
    return seal_backup(dict(schema=SCHEMA,backup_id=str(uuid4()),created_utc=now(),snapshot_utc=snapshot['generated_utc'],
        source=dict(firmware=snapshot['firmware'],library_uuid=snapshot['mapping']['calibre_library_uuid']),
        collections=[dict(id=cs[cid],name=cat.collections[cid]['name']) for cid in cs],books=books,
        relations=relations,unresolved_relations=unknown))

def new_session(state,library,snapshot,backup):
    validate_backup(backup)
    value=dict(id=str(uuid4()),kind='migration',profile=state.key(library,snapshot['device']),revision=0,
        library=library,device=deepcopy(snapshot['device']),backup=deepcopy(backup),created=now(),
        selected=[c['id'] for c in backup['collections']],targets={},matches={},skipped=[],done={},created_targets=[],
        active_job='',stopped=False)
    return state.save_workspace(value,0)

def bind_defaults(session,snapshot):
    value=deepcopy(session);cat=Catalog(snapshot)
    source_names=defaultdict(list)
    for c in value['backup']['collections']:source_names[c['name'].casefold()].append(c)
    target_names=defaultdict(list)
    for c in snapshot['collections']:target_names[c['name'].casefold()].append(c)
    for c in value['backup']['collections']:
        if c['id'] in value['targets']:continue
        candidates=target_names[c['name'].casefold()]
        if len(source_names[c['name'].casefold()])!=1:continue
        if not candidates:value['targets'][c['id']]=dict(id=str(uuid4()),name=c['name'],new=True)
        elif len(candidates)==1 and candidates[0]['name']==c['name']:
            value['targets'][c['id']]=dict(id=candidates[0]['uuid'],name=c['name'],new=False)
    return value

def resolve_books(session,snapshot):
    available={b['uuid']:b for b in snapshot['books'] if not (b.get('location') or '').lower().endswith('.sh')}
    byuid=defaultdict(list)
    for b in available.values():
        if b['calibre_uuid'] and b['metadata_matches']==1:byuid[b['calibre_uuid']].append(b['uuid'])
    same=(session['backup']['source'].get('library_uuid')==session['library']
          and snapshot['mapping']['stable'] and snapshot['mapping'].get('calibre_library_uuid') in (None,session['library']))
    matches={};rows=[]
    for b in session['backup']['books']:
        checkpoint();manual=session['matches'].get(b['id']);candidates=byuid.get(b.get('calibre_uuid'),[]) if same else []
        if b['id'] in session['skipped']:status='用户跳过';chosen=[]
        elif manual is not None:
            chosen=manual if manual and all(i in available for i in manual) else []
            status='人工配对' if chosen else '配对目标已变化'
        elif len(candidates)==1:chosen=candidates;status='已匹配'
        else:chosen=[];status='多个副本，需选择' if len(candidates)>1 else '待传入 / 待匹配'
        matches[b['id']]=chosen;rows.append(dict(id=b['id'],title=b['title'],status=status,targets=chosen))
    return matches,rows

def overview(session,snapshot):
    cat=Catalog(snapshot);matches,bookrows=resolve_books(session,snapshot);relations=defaultdict(set);unknown=defaultdict(int)
    for cid,bid in session['backup']['relations']:relations[cid].add(bid)
    for r in session['backup']['unresolved_relations']:unknown[r['collection']]+=1
    rows=[]
    for c in session['backup']['collections']:
        target=session['targets'].get(c['id']);cid=target['id'] if target else None
        wanted={bid for key in relations[c['id']] for bid in matches.get(key,[])}
        done=set(session['done'].get(c['id'],[]));present=cat.members.get(cid,set())
        changed=done-present;missing=wanted-present-done
        waiting=sum(not matches.get(bid) and bid not in session['skipped'] for bid in relations[c['id']])+unknown[c['id']]
        status='新建' if target and target['new'] and cid not in cat.collections else '合并 / 已有'
        if not target:status='需选择收藏夹目标'
        elif cid not in cat.collections and (not target['new'] or cid in session['created_targets']):status='目标已消失，请重新选择'
        elif cid in cat.collections and (not cat.collections[cid]['complete'] or cid in snapshot['policy']['protected_collections']):status='目标受保护或含未知关系'
        if changed:status+='；迁移后已变更 '+str(len(changed))+' 条'
        rows.append(dict(id=c['id'],name=c['name'],target=target,status=status,wanted=sorted(wanted),
            missing=sorted(missing),existing=len(wanted&present),waiting=waiting,changed=len(changed),
            skipped=sum(bid in session['skipped'] for bid in relations[c['id']])))
    return rows,bookrows

def fold_receipt(session,job,confirmed):
    session=deepcopy(session)
    bindings={b['alias']:b['uuid'] for b in (job.get('result') or {}).get('book_bindings',[])}
    for field in ('matches','done'):
        session[field]={key:sorted({bindings.get(uid,uid) for uid in ids}) for key,ids in session[field].items()}
    for op in confirmed:
        for source in job['migration_sources'].get(op['op_id'],[]):
            if op['kind']=='create_collection':
                session['created_targets']=sorted(set(session['created_targets'])|{op['collection_uuid']})
            if op['kind']=='add_members':
                session['done'][source]=sorted(set(session['done'].get(source,[]))|{bindings.get(uid,uid) for uid in op['args']['members']})
    if job['status']=='complete' and session['active_job']==job['request']['job_id']:session['active_job']=''
    session['last_job']=job['request']['job_id'];session['updated']=now()
    session['last_execution_snapshot']=job['request']['snapshot_digest']
    return session

def progress(session,snapshot):
    rows,_=overview(session,snapshot);rows=[r for r in rows if r['id'] in session['selected']]
    missing=sum(len(r['missing']) for r in rows);waiting=sum(r['waiting'] for r in rows)
    pending=sum(r['status'].startswith(('新建','目标','需选择')) for r in rows)
    if session['stopped']:return '已停止'
    if session['active_job']:return '上一批等待核对'
    if not rows:return '尚未选择收藏夹'
    if not missing and not waiting and not pending:
        return '所选迁移已完成'+('（含用户跳过或后续变更）' if any(r['skipped'] or r['changed'] for r in rows) else '')
    return f'待加入 {missing} 条；待匹配/未知 {waiting} 项；待处理收藏夹 {pending} 个'

class MigrationService:
    def __init__(self,service):self.service=service;self.state=service.state
    def session(self,ident,library,snapshot):
        s=self.state.workspace(ident)
        if not s or s['library']!=library or s['device']!=snapshot['device']:raise Invalid('迁移记录不属于当前书库和设备')
        return s
    def prepare(self,api,library,snapshot,ident):
        s=self.session(ident,library,snapshot)
        if s['stopped']:raise Invalid('迁移已停止，可在迁移记录中继续')
        if s['active_job']:raise Invalid('请先在任务记录核对或恢复上一批迁移任务')
        if s.get('last_execution_snapshot')==digest(snapshot):raise Invalid('请先读取执行后的最新设备状态，再生成下一批迁移')
        key=self.service.key(library,snapshot)
        for j in self.state.job_summaries(key):
            if j['status'] not in ('complete','closed') or self.state.column_pending(j['job_id']):raise Invalid('请先处理当前设备原任务和待回填结果')
            job=self.state.job(j['job_id'])
            if job.get('column_field') and job.get('result'):
                confirmed={r['op_id'] for r in job['result']['operations'] if r['status']=='confirmed'}
                changes={o['op_id'] for o in job['request']['operations'] if o['kind']!='verify_state'}
                if (confirmed&changes)-self.state.baseline_done(j['job_id']):raise Invalid('请先回填已确认的任务结果，再继续迁移')
        p=self.service.profile(library,snapshot)
        from .usability import uses_column
        if p.get('sync_mode')=='column' and not p['field']:raise Invalid('请先在同步设置选择书架列，或明确切换纯 kc-ivy 模式')
        m=read_metadata(api,[p['field']] if uses_column(p) else [])
        if uses_column(p):
            assert_library(snapshot,library,m)
            if p.get('scope_mode','selected')=='all':
                from .scope import expand
                expanded,count=expand(snapshot,m,p)
                if count:p=self.state.save(key,expanded,p['revision'])
        s=bind_defaults(s,snapshot);cat=Catalog(snapshot);rows,books=overview(s,snapshot)
        operations=[];sources={};problems=[];created=0;seen_groups={}
        from .rules import match
        for row in rows:
            if row['id'] not in s['selected']:continue
            target=row['target']
            if not target or row['status'].startswith(('目标','需选择')):
                problems.append(row['name']+'：'+row['status']);continue
            cid=target['id'];is_new=cid not in cat.collections
            if match(target['name'],p['settings']['ignore_all'],p['settings']['ignore_case']):
                problems.append(row['name']+'：全局保护规则阻止修改');continue
            if is_new and created>=snapshot['policy']['max_creates']:
                problems.append(row['name']+'：留到下一批创建');continue
            members=set(cat.members[cid])|set(row['missing'])
            if len(members)>snapshot['policy']['max_members']:
                problems.append(row['name']+'：最终成员超过保护上限，不能分批绕过');continue
            # Conservative bound over full final member list; device rechecks exact payload.
            if len(canonical(dict(name=target['name'],members=sorted(members))))*4+4096>snapshot['policy']['max_request_bytes']:
                problems.append(row['name']+'：请求预计超出保护上限');continue
            if cid in seen_groups:
                problems.append(row['name']+'：本批目标与其他来源收藏夹重合，下一批再核对合并');continue
            seen_groups[cid]=row['id']
            # Remember observed existing edges, so later user removals are not resurrected.
            s['done'][row['id']]=sorted(set(s['done'].get(row['id'],[]))|(set(row['wanted'])&cat.members[cid]))
            if not is_new:s['created_targets']=sorted(set(s['created_targets'])|{cid})
            if is_new:
                op=intent('create_collection',cid,dict(name=target['name']),'迁移：新建目标收藏夹');operations.append(op);sources[op['intent_id']]=[row['id']];created+=1
            if row['missing']:
                op=intent('add_members',cid,dict(members=row['missing']),'迁移：恢复缺少的归属',
                    [operations[-1]['intent_id']] if is_new else [])
                operations.append(op);sources[op['intent_id']]=[row['id']]
        s=self.state.save_workspace(s,s['revision'])
        value=prepare(snapshot,m,p,operations,include_sources=False)
        value.update(profile_key=key,migration_id=ident,migration_revision=s['revision'],migration_sources=sources,
                     migration_notes=problems,migration_rows=rows,origin='migration')
        value['inputs'].update(migration_id=ident,migration_revision=s['revision'],backup_digest=s['backup']['digest'])
        from .planner import plan
        value['plan']=plan(snapshot,operations,{o['collection_uuid'] for o in operations},library,value['inputs'])
        return value
    def submit(self,api,library,store,prepared):
        snapshot=store.snapshot()
        s=self.session(prepared['migration_id'],library,snapshot)
        if s['revision']!=prepared['migration_revision'] or s['active_job']:raise Invalid('迁移选择已变化，请重新预览')
        p=self.service.profile(library,snapshot)
        from .usability import uses_column
        m=read_metadata(api,[p['field']] if uses_column(p) else [])
        inputs=dict(prepared['inputs'],metadata=m['fingerprint'],profile_revision=p['revision'])
        if prepared['issues']:raise Invalid('迁移预览有阻断项')
        from .deferred import extend_request
        req=extend_request(request(prepared['plan'],snapshot,digest(inputs)),getattr(store,'new_books',[]))
        job={k:v for k,v in prepared.items() if k not in ('plan','migration_rows')}
        job.update(request=req,status='staged',column_field=p['field'] if uses_column(p) else '')
        self.state.stage(prepared['profile_key'],job);self.service.publish(store,req)
        return req['job_id']
    def recover(self,library,store,job):
        snapshot,restored=self.service.recover_draft(library,store,job,close=True)
        s=self.session(job['migration_id'],library,snapshot)
        confirmed={o['op_id'] for o in (job.get('result') or {}).get('operations',[]) if o['status']=='confirmed'}
        remaining=[o for o in job['request']['operations'] if o['op_id'] not in confirmed and o['kind']!='verify_state']
        aliases={old:new for op,value in zip(remaining,restored)
                 for old,new in zip(op['args'].get('members',[]),value['args'].get('members',[]))}
        s['matches']={key:[aliases.get(uid,uid) for uid in ids] for key,ids in s['matches'].items()}
        s['active_job']='';return snapshot,self.state.save_workspace(s,s['revision'])
