"""Resolve USB mounts using Calibre first; bounded Windows volume fallback."""
import json
import os
from pathlib import Path
from .protocol import Invalid


def windows_volumes():
    """Read local volume labels without WMI, elevation or recursive book scans."""
    if os.name != 'nt':
        return []
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetLogicalDrives.restype = wintypes.DWORD
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel.GetDriveTypeW.restype = wintypes.UINT
    kernel.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetVolumeInformationW.restype = wintypes.BOOL
    mask = kernel.GetLogicalDrives()
    result = []
    for index in range(26):
        if not mask & (1 << index):
            continue
        root = chr(65 + index) + ':\\'
        if kernel.GetDriveTypeW(root) not in (2, 3):
            continue  # No network drives or optical media.
        label = ctypes.create_unicode_buffer(261)
        if kernel.GetVolumeInformationW(root, label, len(label), None, None, None, None, 0):
            result.append((Path(root), label.value))
    return result


def kc_package(mount):
    folder = mount / 'kmc/kpm/packages/kc'
    try:
        manifest = folder / 'manifest.json'
        if not folder.resolve().is_relative_to(mount.resolve()) or manifest.stat().st_size > 16384:
            return False
        return (json.loads(manifest.read_text(encoding='utf-8-sig')).get('id') == 'kc'
                and (folder / 'sync.sh').is_file())
    except (OSError, ValueError, AttributeError):
        return False


def kindle_layout(mount):
    return (mount / 'documents').is_dir() and (
        kc_package(mount) or (mount / 'kc-sync/state/device.json').is_file()
        or ((mount / 'system').is_dir() and (mount / 'audible').is_dir()))


def resolve_mount(manager):
    # Never rediscover a device Calibre has explicitly disconnected/ejected.
    if not manager.is_device_present:
        raise Invalid('Calibre 尚未连接 Kindle，或设备已弹出。即使 Windows 显示盘符，也请等 Calibre 识别设备后重试。')
    device = manager.connected_device
    prefix = getattr(device, '_main_prefix', None)
    driver = (type(device).__name__ + ' ' + str(getattr(device, 'name', ''))).casefold()
    if prefix:
        try:
            mount = Path(prefix).resolve(strict=True)
        except OSError as exc:
            raise Invalid('Calibre 提供的设备盘已不可访问，请重新连接：' + str(prefix)) from exc
        # A current KC identity also supports existing fixtures and renamed volumes.
        if (kc_package(mount) or (mount / 'kc-sync/state/device.json').is_file()
                or ('kindle' in driver and (mount / 'documents').is_dir())):
            return mount
        labels = {p.resolve(): label.strip().casefold() for p, label in windows_volumes()}
        if labels.get(mount) == 'kindle' and kindle_layout(mount):
            return mount
        raise Invalid('已找到设备盘 ' + str(mount) + '，但无法确认它是 Kindle；请检查卷标和 Kindle 目录。')
    # A missing path is not permission to switch from a different reader or MTP.
    if 'kindle' not in driver or 'mtp' in driver:
        raise Invalid('当前设备没有可用的 Kindle USB 磁盘路径；MTP 连接暂不支持。')
    candidates = [p.resolve() for p, label in windows_volumes()
                  if label.strip().casefold() == 'kindle' and kindle_layout(p)]
    candidates = sorted(set(candidates), key=str)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise Invalid('发现多个 Kindle 候选盘，未选择或写入：' + '、'.join(map(str, candidates))
                      + '。请仅连接要操作的一台 Kindle 后重试。')
    raise Invalid('未找到可确认的 Kindle 盘。请检查 USB 存储连接；自动补充识别需要 Kindle 卷标及设备目录。')
