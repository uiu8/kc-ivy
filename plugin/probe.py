"""An explicit, fixed, narrowly scoped native capability test."""
from copy import deepcopy
from uuid import uuid4
from .protocol import KINDS,Invalid
from .planner import Catalog,intent,plan,request


def prepare_probe(snapshot,book_uuid,library):
    from .deferred import pending
    if pending(book_uuid):raise Invalid('能力测试需使用 Kindle 已识别的测试书；请先弹出刷新收藏夹并重连')
    cat=Catalog(snapshot)
    if book_uuid not in cat.books or cat.book_collections[book_uuid] or cat.books[book_uuid]['collection_count']!=0:
        raise Invalid('能力测试需要一本当前未归类、计数为 0 的测试书，才能验证归零行为')
    cid=str(uuid4()); name='KC-PROBE-'+cid
    ops=[intent('create_collection',cid,dict(name=name),'专用空架测试'),
         intent('rename_collection',cid,dict(name=name+'-RENAMED'),'同 UUID 改名测试'),
         intent('add_members',cid,dict(members=[book_uuid]),'测试加入'),
         intent('remove_members',cid,dict(members=[book_uuid]),'测试归零'),
         intent('add_members',cid,dict(members=[book_uuid]),'准备有成员删架测试'),
         intent('delete_collection',cid,reason='删除专用测试架，书籍应保留')]
    simulated=deepcopy(snapshot); simulated['capabilities']['verified_operations']=list(KINDS)
    # Planner intentionally refuses mixed edit+delete in normal business plans;
    # the narrowly fixed probe below permits exactly this six-step sequence.
    from .planner import state_digest,Plan
    from .protocol import canonical,digest,seal,validate
    name_now=None; members=set(); operations=[]
    for i,op in enumerate(ops):
        before=state_digest(name_now,members) if name_now is not None else None
        if i==0: name_now=name
        elif i==1: name_now=name+'-RENAMED'
        elif i in (2,4): members={book_uuid}
        elif i==3: members=set()
        else: name_now=None; members=set()
        operations.append(dict(op_id=op['intent_id'],kind=op['kind'],collection_uuid=cid,
            before=before,after=state_digest(name_now,members) if name_now is not None else None,args=op['args'],
            depends_on=[operations[-1]['op_id']] if operations else [],reason=op['reason']))
    req=seal(dict(schema='kc-edit-request/v1',job_id=str(uuid4()),plan_id=str(uuid4()),
        plan_digest=digest(operations),device=snapshot['device'],library_uuid=library,snapshot_digest=digest(snapshot),
        policy_version=snapshot['policy']['version'],capabilities_version=snapshot['capabilities']['version'],operations=operations),'request_digest')
    return validate(req)


def cancellable_probe(store):
    """Only the marked six-step probe with no execution evidence may be retired."""
    import re
    from .transport import read
    from .protocol import validate
    if getattr(store,'experimental_mtp',False):raise Invalid('撤销能力测试暂仅支持 USB 磁盘连接')
    store.check_identity()
    marker=read(store.path('state/probe.json'));jid=marker.get('job_id')
    if not isinstance(jid,str) or not re.fullmatch(r'[0-9a-f-]{36}',jid):raise Invalid('能力测试标记无效')
    if (store.path('state/pending.json').exists() or store.path('state/jobs/'+jid).exists()
            or store.path('results/'+jid+'.json').exists() or list(store.root.rglob('*.partial'))):
        raise Invalid('已存在执行记录、回执或中断文件；不能撤销，请核验原测试')
    source=store.path('inbox/'+jid+'.json')
    archived=store.path('state/probe-cancelled/'+jid+'/request.json')
    task=validate(read(source if source.exists() else archived))
    ops=task['operations'];cid=ops[0]['collection_uuid'] if ops else ''
    if (task['device']!=store.device or task['job_id']!=jid
            or [o['kind'] for o in ops]!=['create_collection','rename_collection','add_members','remove_members','add_members','delete_collection']
            or any(o['collection_uuid']!=cid for o in ops)
            or ops[0]['args'].get('name')!='KC-PROBE-'+cid):
        raise Invalid('标记未指向专用能力测试，不会撤销普通任务')
    return task


def cancel_probe(store,expected):
    from .transport import read
    lock=store.path('state/kcpp-transfer.lock');lock.mkdir()
    try:
        task=cancellable_probe(store)
        if task!=expected:raise Invalid('能力测试已变化，请重新核对')
        jid=task['job_id'];folder=store.path('state/probe-cancelled/'+jid);folder.mkdir(parents=True,exist_ok=True)
        source=store.path('inbox/'+jid+'.json');target=folder/'request.json'
        if source.exists():
            if target.exists():raise Invalid('撤销归档已存在，保留原文件等待核查')
            source.rename(target)
        # Retry after a disconnect can finish moving the marker using the archived request.
        store.path('state/probe.json').rename(folder/'marker.json')
        return jid
    finally:lock.rmdir()
