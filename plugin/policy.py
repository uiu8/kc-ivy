from uuid import uuid4
from .protocol import Invalid,seal,validate
from .transport import atomic_write,read

def build(snapshot,protected):
    if not set(protected).issubset({c['uuid'] for c in snapshot['collections']}):raise Invalid('保护范围包含未知收藏夹')
    p=dict(snapshot['policy'],version=snapshot['policy']['version']+1,protected_collections=sorted(set(protected)))
    return validate(seal(dict(schema='kc-policy-request/v1',kind='set_protection_policy',job_id=str(uuid4()),
        device=snapshot['device'],before_version=snapshot['policy']['version'],policy=p),'request_digest'))

def send(store,task):
    if getattr(store,'experimental_mtp',False):return store.send_policy(task)
    validate(task);store.check_identity();snap=store.snapshot()
    if task['device']!=store.device or task['before_version']!=snap['policy']['version']:raise Invalid('策略预览已过期')
    lock=store.path('state/kcpp-transfer.lock');lock.mkdir()
    try:
        if store.path('state/pending.json').exists() or list(store.root.rglob('*.partial')):raise Invalid('设备仍有未完成操作')
        for file in store.path('inbox').glob('*.json'):
            request=read(file)
            result=store.path('results/'+request['job_id']+'.json')
            if not result.exists() or any(r['status']=='pending' for r in read(result)['operations']):raise Invalid('保护策略必须与业务任务分开发送')
        folder=store.path('policy-inbox');folder.mkdir(exist_ok=True)
        if any(folder.glob('*.json')):raise Invalid('已有保护策略等待执行')
        atomic_write(folder/(task['job_id']+'.json'),task)
    finally:lock.rmdir()
