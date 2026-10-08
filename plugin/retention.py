"""Keep five completed and reconciled jobs; incomplete evidence stays intact."""
import re
from .protocol import Invalid
from .transport import read
from .ledger import validate_receipt

def eligible(state,job):
    if job.get('migration_id') and not job.get('migration_folded'):return False
    result=job.get('result')
    if job['status']!='complete' or not result:return False
    states=validate_receipt(job['request'],result)
    if not all(r['status']=='confirmed' for r in states.values()):return False
    jid=job['request']['job_id']
    if state.column_pending(jid):return False
    changed={o['op_id'] for o in job['request']['operations'] if o['kind']!='verify_state'}
    return not job.get('column_field') or changed.issubset(state.baseline_done(jid))

def safe_path(root,path):
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):raise Invalid('历史记录路径异常，已停止清理')
    parent=path.parent
    while parent!=root:
        if parent.is_symlink():raise Invalid('历史目录为链接，已停止清理')
        parent=parent.parent

def prune_completed(store,state,key):
    store.check_identity()
    if store.path('state/pending.json').exists() or list(store.root.rglob('*.partial')):return 0
    summaries=state.job_summaries(key)
    completed=[state.job(s['job_id']) for s in summaries if s['status']=='complete']
    # Retain newest five completions whether or not column processing finished.
    candidates=[j for j in completed[5:] if eligible(state,j)]
    lock=store.path('state/kcpp-transfer.lock');lock.mkdir();count=0
    try:
        for job in candidates:
            jid=job['request']['job_id']
            if job['request']['device']!=store.device:continue
            if not re.fullmatch(r'[0-9a-fA-F-]{36}',jid):continue
            request=store.path('inbox/'+jid+'.json');result=store.path('results/'+jid+'.json');directory=store.path('state/jobs/'+jid)
            paths=[request,result,directory]
            for path in paths:safe_path(store.root,path)
            if request.exists() and read(request)!=job['request']:raise Invalid('历史请求不匹配，保留原记录')
            if result.exists() and read(result)!=job['result']:raise Invalid('历史回执不匹配，保留原记录')
            files=list(directory.iterdir()) if directory.exists() else []
            if any(not f.is_file() or f.is_symlink() or not re.fullmatch(r'(request|progress|book-bindings)\.json|op-\d+\.confirmed\.json',f.name) for f in files):continue
            for path in files:safe_path(store.root,path)
            job_request=directory/'request.json'
            if job_request.exists() and read(job_request)!=job['request']:raise Invalid('执行记录不匹配，保留原记录')
            # Remove executable inbox first. A crash leaves the local receipt and
            # baseline intact, so the next connection can finish pruning safely.
            if request.exists():request.unlink()
            for path in files:path.unlink()
            if directory.exists():directory.rmdir()
            if result.exists():result.unlink()
            state.drop_completed(jid,job['result']['result_digest']);count+=1
    finally:lock.rmdir()
    if count:
        with state.connect() as db:db.execute('VACUUM')
    return count

def automatic_retention(store,state,key):
    if getattr(store,'experimental_mtp',False):return '实验性 MTP：暂保留历史文件，不自动删除设备记录。'
    from .storage import cleanup_plan,cleanup
    count=prune_completed(store,state,key)
    try:
        items=cleanup_plan(store,state.jobs(key))
        removed=cleanup(store,state.jobs(key),items) if items else 0
        return f'历史保留最近5次：清理 {count} 个已完成任务、{removed} 个旧诊断文件。'
    except Invalid as e:return f'已清理 {count} 个已完成任务；诊断记录暂保留：{e}'
