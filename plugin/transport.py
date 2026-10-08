"""Automatic USB exchange. Call through Calibre's serialized device-job queue."""
import os
from pathlib import Path
from uuid import uuid4
from .protocol import Invalid, DEVICE, canonical, check, digest, loads, validate
from .ledger import validate_receipt

SYNC_DIR = 'kc-sync'


def archive_snapshot_partial(store):
    """USB recovery of the one regenerable artifact, never a pending edit."""
    store.check_identity()
    lock=store.path('state/kcpp-transfer.lock');lock.mkdir()
    try:
        source=store.path('snapshots/latest.json.partial')
        if source.is_symlink():raise Invalid('快照残留是链接，停止处理')
        if not source.exists():return None
        if not source.is_file():raise Invalid('快照残留不是普通文件')
        directory=store.path('recovery');directory.mkdir(exist_ok=True)
        target=store.path('recovery/snapshot-'+str(uuid4())+'.json.interrupted')
        if target.exists():raise Invalid('归档名称冲突')
        os.rename(source,target)
        return str(target)
    finally:
        lock.rmdir()


def read(path):
    with (Path(path) if isinstance(path, (str, os.PathLike)) else path).open('rb') as stream:
        return loads(stream.read(64 * 1024 * 1024 + 1))


def atomic_write(path, value):
    """Publish fully flushed bytes; callers hold a lock and prohibit overwrite.

    A leftover .partial is deliberately retained if publication fails. FAT rename
    isn't a promise of power-loss durability; damaged state must be investigated.
    """
    path = Path(path)
    partial = path.with_name(path.name + '.partial')
    raw = canonical(value)
    if path.exists() or partial.exists():
        raise Invalid('目标或未完成文件已存在，不能覆盖：' + path.name)
    with partial.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if partial.read_bytes() != raw:
        raise Invalid('写入回读不一致')
    os.rename(partial, path)
    if path.read_bytes() != raw:
        raise Invalid('发布回读不一致')


class DeviceStore:
    def __init__(self, mount, expected_device=None):
        self.mount = Path(mount).resolve(strict=True)
        self.root = self.mount / SYNC_DIR
        if not self.root.is_dir():
            raise Invalid('设备尚无 KC 新版状态。安全弹出后在 Kindle 运行 KC 刷新，再连接即可自动读取。')
        self.path('state/device.json')
        self.device = read(self.path('state/device.json'))
        check(DEVICE, self.device)
        if expected_device is not None and self.device != expected_device:
            raise Invalid('设备身份变化，需要重新绑定；不会把旧任务发给另一设备')
        self.check_identity()

    def path(self, relative):
        path = self.root / relative
        resolved = path.resolve()
        if not resolved.is_relative_to(self.mount) or not resolved.is_relative_to(self.root.resolve()):
            raise Invalid('设备协议路径越界')
        return path

    def check_identity(self):
        if read(self.path('state/device.json')) != self.device:
            raise Invalid('设备绑定已改变')
        drive = self.mount / 'driveinfo.calibre'
        if drive.exists() and self.device['storage_uuid']:
            if read(drive).get('device_store_uuid') != self.device['storage_uuid']:
                raise Invalid('存储身份不匹配，可能复制了其他设备的 KC 目录')

    def snapshot(self,scan_new=True):
        self.check_identity()
        value = validate(read(self.path('snapshots/latest.json')))
        if value['schema'] != 'kc-bookshelf-export/v2' or value['device'] != self.device:
            raise Invalid('快照与当前设备不匹配')
        from .deferred import discover
        if scan_new:value,self.new_books=discover(self,value)
        else:self.new_books=[]
        return value

    def receipts(self):
        rows = []
        for path in sorted(self.path('results').glob('*.json')):
            value = validate(read(path))
            if value['schema'] not in ('kc-edit-result/v1','kc-edit-result/v2') or value['device'] != self.device:
                raise Invalid('设备目录包含未知或错误设备的回执')
            request = validate(read(self.path('inbox/' + value['job_id'] + '.json')))
            validate_receipt(request, value)
            rows.append(value)
        return rows

    def require_runtime(self, request):
        if request['schema']=='kc-edit-request/v2':
            from .runtime_versions import REF
            entry=self.mount/'documents/KC执行收藏夹任务.sh'
            active=[v.decode('ascii') for v in REF.findall(entry.read_bytes())] if entry.is_file() else []
            if not active or any(tuple(map(int,v.split('.')))<(0,6,14) for v in active):raise Invalid('新书任务需要先安装配套 KC 0.6.14；已发送的旧任务不受影响')

    def send(self, request):
        validate(request)
        from .deferred import extend_request
        # A v1 task containing temporary book IDs must never reach the Kindle executor.
        if extend_request(request,request.get('new_books',[]))!=request:
            raise Invalid('新书任务缺少文件描述，请重新预览')
        if request['schema'] not in ('kc-edit-request/v1','kc-edit-request/v2') or request['device'] != self.device:
            raise Invalid('任务与设备不匹配')
        self.require_runtime(request)
        # job_id is a filename component; the protocol already forbids separators.
        if request['job_id'] in ('.', '..'):
            raise Invalid('无效任务 ID')
        lock = self.path('state/kcpp-transfer.lock')
        lock.mkdir()  # Do not reclaim an uncertain transfer lock.
        try:
            self.check_identity()
            target = self.path('inbox/' + request['job_id'] + '.json')
            if target.exists():
                if validate(read(target))!=request:raise Invalid('同一任务编号对应不同内容')
                return target  # Already published; do not republish against a newer snapshot.
            snap = self.snapshot()
            if request['snapshot_digest'] != digest(snap):
                raise Invalid('快照已更新，原 Preview 失效')
            if (request['policy_version'] != snap['policy']['version'] or
                    request['capabilities_version'] != snap['capabilities']['version']):
                raise Invalid('保护或能力版本已改变')
            if any(o['kind'] not in snap['capabilities']['verified_operations'] for o in request['operations']):
                raise Invalid('设备尚未验证所需编辑能力')
            if any(o['collection_uuid'] in snap['policy']['protected_collections'] and o['kind'] != 'verify_state'
                   for o in request['operations']):
                raise Invalid('任务涉及受保护收藏夹')
            if self.path('state/pending.json').exists():
                raise Invalid('设备仍有未确认操作，请先在 Kindle 核验')
            if any(self.path('policy-inbox').glob('*.json')):
                raise Invalid('先完成独立保护策略任务，再重新预览业务编辑')
            if list(self.root.rglob('*.partial')):
                raise Invalid('存在未完成文件，不能覆盖或忽略')
            target = self.path('inbox/' + request['job_id'] + '.json')
            for path in self.path('inbox').glob('*.json'):
                previous = validate(read(path))
                if previous['schema'] not in ('kc-edit-request/v1','kc-edit-request/v2') or previous['device'] != self.device:
                    raise Invalid('任务目录包含未知或其他设备的数据')
                if previous['job_id'] == request['job_id']:
                    if previous != request:
                        raise Invalid('相同任务 ID 对应不同内容')
                    return path
                result_path = self.path('results/' + previous['job_id'] + '.json')
                if not result_path.exists():
                    raise Invalid('还有待执行任务，不能覆盖或重复排队')
                rows = validate_receipt(previous, read(result_path))
                if any(r['status'] == 'pending' for r in rows.values()):
                    raise Invalid('前一任务仍有未确认项')
            self.check_identity()
            atomic_write(target, request)
            return target
        finally:
            lock.rmdir()


def connected_store(manager, expected_device=None):
    # Evaluate inside create_job, not before queueing: eject/drive changes can occur.
    from .mtp import is_mtp, enabled, MTPStore
    if is_mtp(manager):
        if not enabled():
            raise Invalid('检测到 MTP 设备。请在「设备维护 → 实验性 MTP 传输」明确开启；尚无 KPW6 真机验证。')
        return MTPStore(manager, expected_device)
    from .mounts import resolve_mount
    return DeviceStore(resolve_mount(manager), expected_device)
