"""Explicit maintenance preview; execution evidence is never deleted."""
import time
from pathlib import Path
from .protocol import Invalid

def inventory(store,local):
    result=[]
    for label,path in [('设备任务请求',store.path('inbox')),('执行回执',store.path('results')),
                       ('数据库备份',store.path('state/backups')),('任务执行记录',store.path('state/jobs')),('配置与其他执行状态',store.path('state')),('设备日志',store.path('logs')),('KC运行版本',store.path('runtime')),('启动入口升级备份',store.path('upgrades')),
                       ('中断快照归档',store.path('recovery')),('最新快照',store.path('snapshots')),
                       ('电脑任务历史与列备份',Path(local))]:
        files=[p for p in path.rglob('*') if p.is_file() and not p.is_symlink()] if path.exists() else []
        if label=='配置与其他执行状态':
            files=[p for p in files if p.relative_to(path).parts[0] not in ('jobs','backups')]
        result.append((label,len(files),sum(p.stat().st_size for p in files)))
    return result

def cleanup_plan(store,jobs,now=None):
    store.check_identity()
    from .transport import read
    for request in store.path('inbox').glob('*.json'):
        result=store.path('results/'+request.name)
        if not result.is_file() or any(r['status']!='confirmed' for r in read(result).get('operations',[])):
            raise Invalid('设备仍有未完成或未知任务，保留诊断记录')
    if store.path('state/pending.json').exists() or list(store.root.rglob('*.partial')):
        raise Invalid('仍有不确定执行或未完成文件，暂不清理')
    if any(j['status'] not in ('complete','closed') or (j.get('result') and any(r['status']!='confirmed' for r in j['result']['operations'])) for j in jobs):
        raise Invalid('仍有未完成任务，先处理任务再清理诊断记录')
    now=now or time.time();items=[]
    for folder,pattern in [('logs','KC*.txt'),('recovery','snapshot-*.json.interrupted')]:
        base=store.path(folder)
        if not base.exists():continue
        if base.is_symlink() or not base.resolve().is_relative_to(store.root.resolve()):raise Invalid('诊断目录路径异常')
        files=sorted((p for p in base.glob(pattern) if p.is_file() and not p.is_symlink()),key=lambda p:p.stat().st_mtime,reverse=True)
        for p in files[5:]:
            stat=p.stat()
            items.append(dict(path=str(p.relative_to(store.root)),size=stat.st_size,mtime_ns=stat.st_mtime_ns))
    return items

def cleanup(store,jobs,approved):
    lock=store.path('state/kcpp-transfer.lock');lock.mkdir()
    try:
        current=cleanup_plan(store,jobs)
        if current!=approved:raise Invalid('文件或任务状态已变化，请重新生成清理预览')
        for item in approved:
            path=store.path(item['path'])
            if not path.resolve().is_relative_to(store.root.resolve()) or path.is_symlink():raise Invalid('文件路径异常')
            path.unlink()
        return len(approved)
    finally:lock.rmdir()
