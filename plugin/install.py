"""Versioned KC deployment. Only known KC entrypoints are replaced, with backups."""
import os
import re
from pathlib import Path
from uuid import uuid4
from .protocol import Invalid,canonical,digest
from .transport import atomic_write
from .mounts import resolve_mount,kc_package

VERSION='0.6.15'
FILES=('run.sh','refresh.sh','policy.sh','export.sql','prepare-edit.sql','verify-edit.sql','validate-request.sql','backup-check.sh','kc-backup-check','mtp.sh','kc-screen')


def known_launcher(path,mount):
    if path.stat().st_size > 16384:return False
    text=path.read_text(encoding='utf-8-sig').replace('\r\n','\n').strip()
    # Match complete scripts, never a substring in an otherwise unknown script.
    if re.fullmatch(r'#!/bin/sh\nexec sh /mnt/us/kc-sync/runtime/[0-9]+\.[0-9]+\.[0-9]+/run\.sh',text):
        return True
    if not kc_package(mount):return False
    if text in ('#!/bin/sh\nexec sh ./sync.sh', '#!/bin/sh\nexec sh ./sync.sh "$@"'):
        return True
    return text == '#!/bin/sh\nKC_DIR=$(CDPATH= cd -P "$(dirname "$0")" && pwd) || exit 1\nexec sh "$KC_DIR/sync.sh" "$@"'

def rollback(manager,backup_path):
    from .transport import read
    mount=resolve_mount(manager)
    backup=Path(backup_path).resolve(strict=True)
    if not backup.is_relative_to(mount/'kc-sync/upgrades'):raise Invalid('不是本设备的入口备份')
    manifest=read(backup/'manifest.json')
    prepared=[]
    for item in manifest['entries']:
        target=(mount/item['path']).resolve()
        if not target.is_relative_to(mount):raise Invalid('回退路径越界')
        data=None
        if item['backup']:
            source=(backup/item['backup']).resolve()
            if not source.is_relative_to(backup):raise Invalid('备份路径越界')
            data=source.read_bytes()
        current=target.read_bytes() if target.exists() else None
        # Restored entries (including removed new launchers) are idempotent.
        if current==data:continue
        if current is None or digest(current.decode('utf8'))!=item['after']:raise Invalid('安装后入口已被修改，停止回退')
        partial=target.with_name(target.name+'.kc-restore')
        if not partial.resolve().is_relative_to(mount):raise Invalid('回退临时路径越界')
        if partial.exists() and (data is None or not data.startswith(partial.read_bytes())):
            raise Invalid('回退临时文件与备份不符，已保留')
        prepared.append((target,data,current))
    for target,data,current in prepared:
        if not target.exists() or target.read_bytes()!=current:raise Invalid('回退期间入口发生变化，请重新核对')
        if data is None:target.unlink()  # only a verified launcher created by this install
        else:
            partial=target.with_name(target.name+'.kc-restore')
            with partial.open('wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
            os.replace(partial,target)
    return len(prepared)

def install(manager,resources):
    from .mtp import is_mtp, enabled, install_mtp
    if is_mtp(manager):
        if not enabled():raise Invalid('请先在设备维护中开启实验性 MTP 传输')
        return install_mtp(manager,resources)
    mount=resolve_mount(manager)
    def safe(relative):
        p=(mount/relative)
        if not p.resolve().is_relative_to(mount):raise Invalid('安装路径越界')
        return p
    old=safe('kmc/kpm/packages/kc/launch.sh')
    if old.exists() and not known_launcher(old,mount):
        raise Invalid('已识别设备盘 '+str(mount)+'，但旧 KC 启动脚本不是支持的版本；未安装，已保留原入口：'+str(old))
    runtime=safe('kc-sync/runtime/'+VERSION)
    # Complete preflight before creating any files, including partially installed runtimes.
    for name in FILES:
        data=resources['runtime/'+name]
        target=safe('kc-sync/runtime/'+VERSION+'/'+name)
        if target.exists() and target.read_bytes()!=data:
            raise Invalid('同版本运行文件不同，停止覆盖：'+name)
        if target.with_suffix(target.suffix+'.partial').exists():
            raise Invalid('发现未完成的运行文件，请先检查：'+name)
    for name in ('KC刷新收藏夹.sh','KC执行收藏夹任务.sh','KC验证编辑能力.sh'):
        target=safe('documents/'+name)
        if target.with_name(target.name+'.kc-partial').exists():raise Invalid('发现未完成入口：'+name)
    if old.with_name(old.name+'.kc-partial').exists():raise Invalid('发现未完成的旧 KC 入口')
    runtime.mkdir(parents=True,exist_ok=True)
    for name in FILES:
        data=resources['runtime/'+name]
        target=runtime/name
        if target.exists():
            if target.read_bytes()!=data:raise Invalid('同版本运行文件不同，停止覆盖：'+name)
            continue
        partial=target.with_suffix(target.suffix+'.partial')
        with partial.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        if partial.read_bytes()!=data:raise Invalid('安装文件回读不同')
        partial.rename(target)
    backup=safe('kc-sync/upgrades/'+str(uuid4()));backup.mkdir(parents=True)
    records=[]
    replacements={
        'documents/KC刷新收藏夹.sh':f'#!/bin/sh\nexec sh /mnt/us/kc-sync/runtime/{VERSION}/run.sh --refresh\n',
        'documents/KC执行收藏夹任务.sh':f'#!/bin/sh\nexec sh /mnt/us/kc-sync/runtime/{VERSION}/run.sh\n',
        'documents/KC验证编辑能力.sh':f'#!/bin/sh\nexec sh /mnt/us/kc-sync/runtime/{VERSION}/run.sh --self-test\n'}
    if old.exists():
        replacements['kmc/kpm/packages/kc/launch.sh']=replacements['documents/KC执行收藏夹任务.sh']
    for relative,body in replacements.items():
        target=safe(relative);target.parent.mkdir(parents=True,exist_ok=True)
        old=target.read_bytes() if target.exists() else None
        if old==body.encode():continue
        if old is not None:
            saved=backup/(str(len(records))+'.bak');saved.write_bytes(old)
        else:saved=None
        records.append(dict(path=relative,backup=saved.name if saved else None,after=digest(body)))
        atomic_write(backup/(str(len(records))+'.record.json'),records[-1])
        partial=target.with_name(target.name+'.kc-partial')
        with partial.open('xb') as stream:stream.write(body.encode());stream.flush();os.fsync(stream.fileno())
        os.replace(partial,target)
    atomic_write(backup/'manifest.json',dict(version=VERSION,entries=records))
    return str(backup)
