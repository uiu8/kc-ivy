"""USB runtime retention. Explicit preview, never automatic; no task data writes."""
import hashlib,re,json
from pathlib import Path
from .protocol import Invalid
from .install import FILES

VERSION=re.compile(r'\d+\.\d+\.\d+')
REF=re.compile(rb'/mnt/us/kc-sync/runtime/(\d+\.\d+\.\d+)/')
LAUNCHERS={'documents/KC刷新收藏夹.sh','documents/KC执行收藏夹任务.sh','documents/KC验证编辑能力.sh','kmc/kpm/packages/kc/launch.sh'}

def checked(root,path):
    if not path.resolve().is_relative_to(root.resolve()):raise Invalid('版本目录路径越界')
    for p in (path,*path.parents):
        if p==root:break
        if p.is_symlink() or getattr(p,'is_junction',lambda:False)():raise Invalid('版本目录包含链接，停止清理')
    return path

def preview(store):
    if getattr(store,'experimental_mtp',False):raise Invalid('实验性 MTP 暂不支持版本清理，请使用 USB 磁盘连接')
    store.check_identity();root=store.root;mount=store.mount
    versions={};backups=[];active=set();fingerprints=[];blocked=[]
    def data(p):
        checked(mount,p);b=p.read_bytes()
        fingerprints.append((str(p.relative_to(mount)),hashlib.sha256(b).hexdigest()))
        return b
    def refs(b):return {x.decode('ascii') for x in REF.findall(b)}
    for base in [mount/'documents',mount/'kmc/kpm/packages']:
        if not base.exists():continue
        checked(mount,base)
        for script in sorted(base.rglob('*.sh')):
            active.update(refs(data(script)))
    if not active:blocked.append('未识别到正在使用的 KC 版本，暂不清理')
    if (root/'state/pending.json').exists():blocked.append('有待核验的执行结果，先处理原任务')
    base=root/'runtime';checked(mount,base)
    for folder in sorted(base.iterdir()) if base.exists() else []:
        checked(mount,folder)
        if not folder.is_dir() or not VERSION.fullmatch(folder.name):
            blocked.append('存在未识别运行目录：'+folder.name);continue
        files=list(sorted(folder.iterdir()));size=0;known=True
        for f in files:
            checked(mount,f)
            if not f.is_file() or f.name not in set(FILES)|{'kc-backup-guard.so'}:
                known=False;continue
            size+=len(data(f))
        versions[folder.name]=dict(version=folder.name,size=size,known=known,files=[f.name for f in files])
    keep=set(active)
    others=sorted(set(versions)-active,key=lambda v:tuple(map(int,v.split('.'))),reverse=True)
    keep.update(others[:2]);keep.update(v for v,row in versions.items() if not row['known'])
    missing=active-set(versions)
    if missing:blocked.append('启动入口引用的运行版本缺失：'+', '.join(sorted(missing)))
    base=root/'upgrades';checked(mount,base)
    for folder in sorted(base.iterdir()) if base.exists() else []:
        checked(mount,folder)
        if not folder.is_dir():blocked.append('未识别入口备份文件：'+folder.name);continue
        files=list(sorted(folder.iterdir()))
        for f in files:checked(mount,f)
        retired=(folder/'manifest.retired.json').exists()
        manifest=folder/('manifest.retired.json' if retired else 'manifest.json')
        if not files:
            backups.append(dict(name=folder.name,size=0,refs=[],remove=True,retired=True,files=[]));continue
        try:
            if not manifest.is_file():raise ValueError('缺少完整备份清单')
            m=json.loads(data(manifest));expected={manifest.name};references=set();size=manifest.stat().st_size
            if not VERSION.fullmatch(m['version']):raise ValueError('无效版本号')
            record_base=0 if (folder/'0.record.json').exists() else 1
            for i,e in enumerate(m['entries']):
                if e['path'] not in LAUNCHERS:raise ValueError('未知入口路径')
                record=str(i+record_base)+'.record.json'
                if (folder/record).exists():
                    if json.loads(data(folder/record))!=e:raise ValueError('入口记录与备份清单不一致')
                    expected.add(record)
                name=e['backup']
                if name is not None:
                    if name!=str(i)+'.bak':raise ValueError('无效备份路径')
                    expected.add(name)
                    if not (folder/name).exists() and not retired:raise ValueError('入口备份不完整')
            for f in files:
                if not f.is_file() or f.name not in expected:raise ValueError('未识别备份文件')
                if f!=manifest:
                    b=data(f);size+=len(b)
                    if f.suffix=='.bak':references.update(refs(b))
            remove=retired or not m['entries'] or m['version'] not in keep or not references.issubset(keep)
            backups.append(dict(name=folder.name,size=size,refs=sorted(references),remove=remove,retired=retired,files=[f.name for f in files]))
        except (ValueError,KeyError,TypeError) as e:
            blocked.append('入口备份 '+folder.name+' 无法安全识别：'+str(e))
    rows=[]
    for v,row in versions.items():
        reason='当前入口使用' if v in active else ('含未知文件，保留' if not row['known'] else '保留旧版' if v in keep else '可清理')
        rows.append(dict(row,status=reason,remove=v not in keep))
    return dict(versions=rows,backups=backups,blocked=blocked,fingerprint=sorted(fingerprints),active=sorted(active))

def cleanup(store,approved):
    lock=store.path('state/kcpp-transfer.lock');lock.mkdir()
    try:
        current=preview(store)
        if current!=approved:raise Invalid('版本、入口或备份已变化，请重新打开版本管理')
        if current['blocked']:raise Invalid('；'.join(current['blocked']))
        # Retire rollback manifests before deleting runtimes. Interrupted deletion
        # remains identifiable and can be resumed from a fresh preview.
        for b in current['backups']:
            if not b['remove']:continue
            folder=checked(store.mount,store.root/'upgrades'/b['name'])
            manifest=folder/'manifest.json';retired=folder/'manifest.retired.json'
            if manifest.exists():manifest.rename(retired)
            for name in b['files']:
                if name in ('manifest.json','manifest.retired.json'):continue
                checked(store.mount,folder/name).unlink()
            if retired.exists():retired.unlink()
            folder.rmdir()
        removed=0
        for row in current['versions']:
            if not row['remove']:continue
            folder=checked(store.mount,store.root/'runtime'/row['version'])
            for name in row['files']:checked(store.mount,folder/name).unlink()
            folder.rmdir();removed+=1
        return removed
    finally:lock.rmdir()
