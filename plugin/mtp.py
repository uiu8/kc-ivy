"""Experimental Calibre MTP transport; queued payloads are published by KC.

Use only from Calibre's device-job thread. New ebooks are streamed to temporary disk files for fingerprinting; database
backups are not downloaded. Listings reuse the current connection cache.
"""
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory, TemporaryFile
from types import SimpleNamespace
import fnmatch
import re
import os

from .protocol import Invalid, DEVICE, check, canonical, digest, validate, checkpoint
from .transport import DeviceStore, read

PREFERENCE = 'experimental_mtp'
LIMIT = 64 * 1024 * 1024


def enabled():
    from calibre.utils.config import JSONConfig
    return bool(JSONConfig('plugins/kc-plus-transport').get(PREFERENCE, False))


def is_mtp(manager):
    device = getattr(manager, 'connected_device', None)
    return (not getattr(device, '_main_prefix', None)
            and callable(getattr(device, 'get_mtp_file', None)))


def can_enable(manager):
    return bool(getattr(manager, 'is_device_present', False) and is_mtp(manager)
                and getattr(manager.connected_device, 'is_kindle', False))


def execution_hint(manager, probe=False):
    mtp = is_mtp(manager)
    entry = 'KC验证编辑能力' if probe else 'KC执行收藏夹任务'
    if mtp:
        entry += '-MTP'
    return ('断开连接' if mtp else '安全弹出') + ' → Kindle 运行“' + entry + '” → 重连读取结果。'


class MTPFiles:
    def __init__(self, manager):
        self.manager = manager
        self.driver = manager.connected_device
        if not manager.is_device_present or not getattr(self.driver, 'is_kindle', False):
            raise Invalid('实验性 MTP 只接受 Calibre 已识别的 Amazon Kindle')
        self.storage_id = self.driver._main_id
        self.cache = self.driver.filesystem_cache
        self.storage = self.cache.storage(self.storage_id)
        if self.storage is None or self.storage.find_path(('documents',)) is None:
            raise Invalid('MTP 主存储没有 documents 目录，停止识别')
        for sub in ('', 'state', 'inbox', 'results', 'snapshots', 'policy-inbox', 'mtp-inbox'):
            parts=('kc-sync', sub) if sub else ('kc-sync',)
            if self.driver.is_folder_ignored(self.storage_id, parts):
                raise Invalid('请先在 Calibre MTP 设置中取消忽略 kc-sync 及其协议子目录，再重连')

    def guard(self):
        if (not self.manager.is_device_present or self.manager.connected_device is not self.driver
                or self.driver._main_id != self.storage_id or self.driver.filesystem_cache is not self.cache):
            raise Invalid('MTP 设备已断开或发生变化，请重新连接')

    def path(self, value=''):
        value = str(value).replace('\\', '/')
        p = PurePosixPath(value)
        if p.is_absolute() or '..' in p.parts or ':' in value or '\x00' in value:
            raise Invalid('MTP 路径越界')
        return MTPPath(self, p)

    def node(self, path):
        self.guard()
        return self.storage.find_path(path.parts) if path.parts else self.storage

    def fetch(self, path):
        self.guard()
        node = self.node(path)
        if node is not None and node.is_folder:
            raise Invalid('MTP 路径是目录：' + str(path))
        if node is not None and node.size > LIMIT:
            raise Invalid('MTP 协议文件超过 64 MiB 限制')
        # By-name reads also work when Calibre has excluded a subtree from its cache.
        with self.driver.get_mtp_file_by_name(self.storage, *path.parts) as stream:
            data = stream.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise Invalid('MTP 协议文件超过 64 MiB 限制')
        return data

    def ebook_digest(self, path):
        self.guard()
        node = self.node(path)
        if node is None or node.is_folder: raise Invalid('新书文件不存在')
        before = (node.object_id, node.size)
        h = sha256(); count = 0
        # Explicit disk backing avoids the protocol reader's 64 MiB limit.
        with TemporaryFile() as stream:
            from .protocol import progress
            def transferred(done,total):
                checkpoint();progress(f'MTP 正在读取 {path.name}：{done}/{total} 字节')
            self.driver.get_mtp_file_by_name(self.storage, *path.parts, stream=stream,callback=transferred)
            stream.seek(0)
            while True:
                checkpoint(); self.guard()
                chunk = stream.read(1024 * 1024)
                if not chunk: break
                count += len(chunk); h.update(chunk)
        after = self.node(path)
        if after is None or (after.object_id, after.size) != before or count != before[1]:
            raise Invalid('传书尚未结束，请等待完成后重新读取')
        return h.hexdigest()

    def upload(self, relative, data, resume=False):
        """Only inert stage/runtime files, never publish directly to inbox."""
        path = self.path(relative)
        if not (str(path.p).startswith('kc-sync/mtp-inbox/')
                or str(path.p).startswith('kc-sync/runtime/')
                or (path.p.parent == PurePosixPath('documents') and path.name.endswith('-MTP.sh'))):
            raise Invalid('实验性 MTP 不允许写入此路径')
        self.guard()
        parent = self.storage
        for component in path.p.parent.parts:
            parent = self.driver.create_folder(parent, component)
        old = parent.file_named(path.name)
        if old is not None:
            existing = self.fetch(path.p)
            if existing == data:
                return
            if not resume or not data.startswith(existing):
                raise Invalid('MTP 目标内容不同，保留原文件：' + str(path))
        self.driver.put_file(parent, path.name, BytesIO(data), len(data), replace=old is not None)
        if self.fetch(path.p) != data:
            raise Invalid('MTP 回读不一致；尚未确认传输，请核对 / 重试原任务')


class MTPPath:
    """Read-only protocol path. Deliberately not an os.PathLike/local filename."""
    def __init__(self, fs, path): self.fs, self.p = fs, path
    def __str__(self): return 'mtp:/' + str(self.p)
    def __truediv__(self, other): return self.fs.path(str(self.p / str(other)))
    def __eq__(self, other): return isinstance(other, MTPPath) and self.fs is other.fs and self.p == other.p
    def __hash__(self): return hash((id(self.fs), self.p))
    def __lt__(self, other): return str(self) < str(other)
    @property
    def name(self): return self.p.name
    @property
    def stem(self): return self.p.stem
    def ebook_digest(self): return self.fs.ebook_digest(self.p)
    @property
    def parent(self): return MTPPath(self.fs, self.p.parent)
    def resolve(self): return self
    def is_relative_to(self, other): return self.fs is other.fs and self.p.is_relative_to(other.p)
    def relative_to(self, other): return self.p.relative_to(other.p)
    def with_name(self, name): return self.fs.path(str(self.p.with_name(name)))
    def is_symlink(self): return False
    def exists(self): return self.fs.node(self.p) is not None
    def is_file(self):
        n = self.fs.node(self.p)
        return n is not None and not n.is_folder
    def is_dir(self):
        n = self.fs.node(self.p)
        return n is not None and n.is_folder
    def read_bytes(self): return self.fs.fetch(self.p)
    def read_text(self, encoding='utf-8'): return self.read_bytes().decode(encoding)
    def open(self, mode='rb'):
        if mode != 'rb': return self.unsupported()
        return BytesIO(self.read_bytes())
    def iterdir(self):
        n = self.fs.node(self.p)
        if n is not None:
            for child in list(n): yield self / child.name
    def glob(self, pattern):
        first, sep, tail = pattern.partition('/')
        for child in self.iterdir():
            if fnmatch.fnmatchcase(child.name, first):
                if sep: yield from child.glob(tail)
                else: yield child
    def rglob(self, pattern):
        for child in self.iterdir():
            if fnmatch.fnmatchcase(child.name, pattern): yield child
            if child.is_dir(): yield from child.rglob(pattern)
    def stat(self):
        n = self.fs.node(self.p)
        if n is None: raise FileNotFoundError(str(self))
        return SimpleNamespace(st_size=n.size, st_mtime=0, st_mtime_ns=0)
    def unsupported(self, *args, **kwargs):
        raise Invalid('此维护操作尚未适配实验性 MTP；文件已保留。可读取结果、发送 / 重试原任务及准备能力测试。')
    mkdir = rmdir = unlink = rename = unsupported


class MTPStore(DeviceStore):
    experimental_mtp = True

    def __init__(self, manager, expected_device=None):
        self.fs = MTPFiles(manager)
        self.mount = self.fs.path()
        self.root = self.mount / 'kc-sync'
        if not self.root.is_dir():
            raise Invalid('请先安装实验性 MTP 配套 KC，断开连接后运行 KC刷新收藏夹-MTP，再重连')
        try:
            self.device = read(self.path('state/device.json'))
        except FileNotFoundError as exc:
            raise Invalid('KC 尚未初始化。请断开连接，运行「KC刷新收藏夹-MTP」，再重连') from exc
        check(DEVICE, self.device)
        if expected_device is not None and self.device != expected_device:
            raise Invalid('MTP 设备身份不匹配')
        self.check_identity()

    def path(self, relative):
        return self.fs.path('kc-sync') / relative

    def calibre_path(self, key):
        parts = self.fs.driver.find_calibre_file_path(self.fs.storage, key)
        return self.fs.path('/'.join(parts))

    def discovery_digest(self, path, metadata):
        token = (self.fs.cache, sha256(metadata).digest())
        cached = getattr(self.fs.driver, '_kc_new_book_hashes', None)
        if cached is None or cached[0] != token:
            cached = (token, {})
            self.fs.driver._kc_new_book_hashes = cached
        node = self.fs.node(path.p)
        key = (str(path.p), node.object_id, node.size)
        if getattr(self, '_fresh_new_books', False) or key not in cached[1]:
            cached[1][key] = path.ebook_digest()
        return cached[1][key]

    def require_runtime(self, request):
        if request['schema'] != 'kc-edit-request/v2': return
        from .runtime_versions import REF
        entry = self.mount / 'documents/KC执行收藏夹任务-MTP.sh'
        active = [v.decode('ascii') for v in REF.findall(entry.read_bytes())] if entry.is_file() else []
        if not active or any(tuple(map(int, v.split('.'))) < (0, 6, 15) for v in active):
            raise Invalid('MTP 新书任务需要先安装配套 KC 0.6.15，再重新预览发送')

    def pending_transfer(self, job_id):
        return self.path('mtp-inbox/' + job_id + '.json').exists()

    @contextmanager
    def validation_copy(self):
        """Reuse USB protocol validation on a small, ephemeral protocol mirror."""
        self.check_identity()
        with TemporaryDirectory(prefix='kcpp-mtp-') as folder:
            base = Path(folder)
            for name in ('inbox', 'results', 'snapshots', 'state', 'policy-inbox'):
                (base / 'kc-sync' / name).mkdir(parents=True)
            names = ['state/device.json', 'snapshots/latest.json']
            if self.path('state/pending.json').exists(): names.append('state/pending.json')
            for name in ('inbox', 'results', 'policy-inbox'):
                names.extend(name + '/' + p.name for p in self.path(name).glob('*.json'))
            if list(self.root.rglob('*.partial')):
                raise Invalid('设备仍有未完成文件，请先处理设备状态')
            for name in names:
                (base / 'kc-sync' / name).write_bytes(self.path(name).read_bytes())
            if (self.mount / 'driveinfo.calibre').exists():
                (base / 'driveinfo.calibre').write_bytes((self.mount / 'driveinfo.calibre').read_bytes())
            local = DeviceStore(base)
            local.snapshot = self.snapshot
            local.require_runtime = self.require_runtime
            yield local

    def queue(self, request, mode):
        self.check_identity()
        feature = read(self.path('state/mtp.json'))
        if feature != {'schema': 'kc-mtp/v1', 'device': self.device}:
            raise Invalid('请先运行新版 KC刷新收藏夹-MTP，再重连以启用传输协议')
        jid = request['job_id']
        if not re.fullmatch(r'[0-9a-fA-F-]{36}', jid): raise Invalid('MTP 任务编号无效')
        directory = self.path('mtp-inbox')
        for p in directory.iterdir():
            if p.name not in (jid + '.json', jid + '.ready'):
                raise Invalid('MTP 仍有另一批待交接文件，请先在 Kindle 运行对应任务')
        self.save_transfer(request, mode)
        raw = canonical(request)
        ready = (sha256(raw).hexdigest() + ' ' + mode + '\n').encode('ascii')
        marker = directory / (jid + '.ready')
        if marker.exists():
            existing = marker.read_bytes()
            if not ready.startswith(existing): raise Invalid('MTP 确认标记不匹配，保留原任务')
            if existing == ready:
                if (directory / (jid + '.json')).read_bytes() != raw:
                    raise Invalid('已确认的 MTP 任务内容变化，停止重发')
                return str(marker)
        self.fs.upload('kc-sync/mtp-inbox/' + jid + '.json', raw, resume=True)
        self.check_identity()
        self.fs.upload('kc-sync/mtp-inbox/' + jid + '.ready', ready, resume=True)
        return str(marker)

    def transfer_record(self):
        from calibre.constants import config_dir
        return Path(config_dir) / 'plugins' / 'kc-plus-mtp' / (digest(self.device) + '.json')

    def save_transfer(self, request, mode):
        path = self.transfer_record()
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix('.partial')
        with partial.open('wb') as stream:
            stream.write(canonical(dict(device=self.device, mode=mode, request=request)))
            stream.flush(); os.fsync(stream.fileno())
        os.replace(partial, path)

    def retry_transfer(self):
        record = read(self.transfer_record())
        if record['device'] != self.device: raise Invalid('本机 MTP 记录属于其他设备')
        task = validate(record['request']); mode = record['mode']
        target = self.path(('policy-inbox/' if mode == 'policy' else 'inbox/') + task['job_id'] + '.json')
        if target.exists():
            if read(target) != task: raise Invalid('设备任务内容与本机记录不符')
            return '原任务已交接，无需重发；请读取结果或在 Kindle 继续原任务。'
        if mode == 'edit': self.send(task)
        elif mode == 'probe': self.send_probe(task)
        elif mode == 'policy': self.send_policy(task)
        else: raise Invalid('未知 MTP 记录类型')
        return '原 MTP 文件已回读确认；请断开连接，运行「KC验证编辑能力-MTP」。' if mode == 'probe' else '原 MTP 文件已回读确认；请断开连接，运行「KC执行收藏夹任务-MTP」。'

    def send(self, request):
        validate(request)
        self._fresh_new_books = True
        try:
            with self.validation_copy() as local:
                local.send(request)
        finally:
            self._fresh_new_books = False
        target = self.path('inbox/' + request['job_id'] + '.json')
        if target.exists():
            if read(target) != request: raise Invalid('任务编号冲突')
            return str(target)
        return self.queue(request, 'edit')

    def send_probe(self, request):
        from .ledger import validate_receipt
        validate(request)
        if request['device'] != self.device or digest(self.snapshot()) != request['snapshot_digest']:
            raise Invalid('测试设备或快照已变化')
        with self.validation_copy() as local:
            if local.path('state/pending.json').exists(): raise Invalid('先处理未确认操作')
            for p in local.path('inbox').glob('*.json'):
                if not local.path('results/' + p.name).exists(): raise Invalid('先完成已发送任务')
        old = self.path('state/probe.json')
        if old.exists():
            jid = read(old)['job_id']
            if not re.fullmatch(r'[0-9a-fA-F-]{36}', jid): raise Invalid('旧测试标记无效')
            states = validate_receipt(read(self.path('inbox/' + jid + '.json')), read(self.path('results/' + jid + '.json')))
            if any(r['status'] != 'confirmed' for r in states.values()): raise Invalid('先完成上一次能力测试')
        return self.queue(request, 'probe')

    def send_policy(self, task):
        from .policy import send
        with self.validation_copy() as local: send(local, task)
        return self.queue(task, 'policy')


def mtp_launchers(resources):
    from .install import FILES, VERSION
    checks = ' &&\n'.join('[ "$(sha256sum /mnt/us/kc-sync/runtime/' + VERSION + '/' + n
        + ' | cut -d \' \' -f 1)" = "' + sha256(resources['runtime/' + n]).hexdigest() + '" ]' for n in FILES)
    result = {}
    for name, option in [('KC刷新收藏夹', '--refresh'), ('KC执行收藏夹任务', ''), ('KC验证编辑能力', '--self-test')]:
        body = '#!/bin/sh\nif ' + checks + '; then\nexec sh /mnt/us/kc-sync/runtime/' + VERSION + '/run.sh ' + option + '\nelse\necho "KC runtime incomplete; reinstall from kc-ivy"\nfi\n'
        result['documents/' + name + '-MTP.sh'] = body.encode('utf-8')
    return result


def install_mtp(manager, resources):
    from .install import FILES, VERSION
    if not can_enable(manager):raise Invalid('当前不是 Calibre 已识别的 MTP Kindle，停止实验安装')
    fs = MTPFiles(manager)
    launchers = mtp_launchers(resources)
    for path, data in launchers.items():
        target = fs.path(path)
        if target.exists() and not data.startswith(target.read_bytes()):
            raise Invalid('实验入口已有不同内容，停止安装并保留原文件：' + path)
    for name in FILES:
        fs.upload('kc-sync/runtime/' + VERSION + '/' + name, resources['runtime/' + name], resume=True)
    for path, data in launchers.items():fs.upload(path, data, resume=True)
    return 'MTP 实验安装完成；原入口保留。请断开连接，运行「KC刷新收藏夹-MTP」，再重连。'


def rollback_mtp(manager, resources):
    """Withdraw only byte-verified experimental launchers, even with opt-in off.

    Never erase runtime, staged work, receipts, original launchers or books.
    Preflight all entries before deleting any. A disconnected deletion can be retried.
    """
    from .ledger import validate_receipt
    if not can_enable(manager):raise Invalid('请连接需要撤下实验入口的 MTP Kindle')
    fs = MTPFiles(manager)
    root = fs.path('kc-sync')
    if (root/'state/pending.json').exists() or list((root/'mtp-inbox').iterdir()) or list((root/'policy-inbox').iterdir()):
        raise Invalid('设备还有未完成或待核验任务；请先继续原任务并读取结果，再撤下实验入口。也可只关闭 MTP 开关，任务文件会保留。')
    for path in (root/'inbox').glob('*.json'):
        result = root/'results'/path.name
        if not result.exists() or any(r['status']=='pending' for r in validate_receipt(read(path),read(result)).values()):
            raise Invalid('仍有任务没有确定结果；请先核验原任务，再撤下实验入口。')
    prepared=[]
    for path, data in mtp_launchers(resources).items():
        target=fs.path(path)
        if not target.exists():continue
        if target.read_bytes()!=data:
            raise Invalid('实验入口内容不同或安装未完成，未删除任何入口。请核对文件，或先重试原安装：'+path)
        prepared.append((target,data))
    for target,data in prepared:
        fs.guard()
        if target.read_bytes()!=data:raise Invalid('入口在检查后发生变化，停止删除；可以重新核对后继续')
        fs.driver.delete_file_or_folder(fs.node(target.p))
        if target.exists():raise Invalid('入口删除未确认，请重连后重试撤下')
    return len(prepared)
