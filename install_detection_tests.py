"""Installation regression: old KC 0.2.0 relative launcher, no real device writes."""
import tempfile
import unittest
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from plugin.install import install, rollback, FILES, VERSION
from plugin.mounts import resolve_mount, windows_volumes
from plugin.protocol import Invalid

ROOT = Path(__file__).resolve().parent


class InstallDetection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.volumes-', dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def volume(self, name):
        mount = self.base / name
        (mount / 'documents').mkdir(parents=True)
        folder = mount / 'kmc/kpm/packages/kc'
        folder.mkdir(parents=True)
        (folder / 'manifest.json').write_text('{"id":"kc"}')
        (folder / 'sync.sh').write_text('#!/bin/sh\n')
        return mount

    def manager(self, prefix=None, name='Kindle', present=True):
        return SimpleNamespace(is_device_present=present,
            connected_device=SimpleNamespace(_main_prefix=prefix, name=name))

    def test_current_path_preferred_even_renamed(self):
        mount = self.volume('renamed')
        with patch('plugin.mounts.windows_volumes', side_effect=AssertionError('unneeded scan')):
            self.assertEqual(resolve_mount(self.manager(mount)), mount.resolve())

    def test_case_insensitive_label_fallback(self):
        mount = self.volume('new-letter')
        with patch('plugin.mounts.windows_volumes', return_value=[(mount, 'KiNdLe')]):
            self.assertEqual(resolve_mount(self.manager()), mount.resolve())

    def test_ambiguous_volumes_refused(self):
        volumes = [(self.volume('one'), 'Kindle'), (self.volume('two'), 'Kindle')]
        with patch('plugin.mounts.windows_volumes', return_value=volumes):
            with self.assertRaisesRegex(Invalid, '多个 Kindle'):
                resolve_mount(self.manager())

    def test_label_alone_refused(self):
        mount = self.base / 'empty'
        (mount / 'documents').mkdir(parents=True)
        with patch('plugin.mounts.windows_volumes', return_value=[(mount, 'Kindle')]):
            with self.assertRaisesRegex(Invalid, '未找到'):
                resolve_mount(self.manager())

    def test_disconnected_or_other_device_never_falls_back(self):
        with patch('plugin.mounts.windows_volumes', side_effect=AssertionError('unsafe fallback')):
            for manager in (self.manager(present=False), self.manager(name='Kobo'),
                            self.manager(name='Kindle MTP'), self.manager(self.base / 'missing')):
                with self.subTest(manager=manager):
                    with self.assertRaises(Invalid): resolve_mount(manager)

    def test_invalid_current_drive_never_switches(self):
        wrong = self.base / 'wrong'
        wrong.mkdir()
        with patch('plugin.mounts.windows_volumes', return_value=[(self.volume('real'), 'Kindle')]):
            with self.assertRaisesRegex(Invalid, '无法确认'):
                resolve_mount(self.manager(wrong))

    def test_unknown_script_refused_before_writing(self):
        mount = self.volume('unknown')
        old = mount / 'kmc/kpm/packages/kc/launch.sh'
        body = '#!/bin/sh\ncustom command\nexec sh ./sync.sh\n'
        old.write_text(body)
        resources = {'runtime/' + name: (ROOT / 'device' / name).read_bytes() for name in FILES}
        with self.assertRaisesRegex(Invalid, '已识别设备盘'):
            install(self.manager(mount), resources)
        self.assertEqual(old.read_text(), body)
        self.assertFalse((mount / 'kc-sync').exists())

    def test_conflicting_runtime_refused_before_writing(self):
        mount = self.volume('conflict')
        runtime = mount / 'kc-sync/runtime' / VERSION
        runtime.mkdir(parents=True)
        (runtime / FILES[-1]).write_bytes(b'unknown')
        resources = {'runtime/' + name: (ROOT / 'device' / name).read_bytes() for name in FILES}
        with self.assertRaisesRegex(Invalid, '同版本运行文件不同'):
            install(self.manager(mount), resources)
        self.assertFalse((runtime / FILES[0]).exists())
        self.assertFalse((mount / 'kc-sync/upgrades').exists())

    def test_kc020_relative_launcher(self):
        with tempfile.TemporaryDirectory(prefix='.detect-', dir=ROOT) as tmp:
            mount = Path(tmp)
            old = mount / 'kmc/kpm/packages/kc/launch.sh'
            old.parent.mkdir(parents=True)
            body = b'#!/bin/sh\nexec sh ./sync.sh\n'
            old.write_bytes(body)
            old.with_name('manifest.json').write_text('{"id":"kc","manifest_version":2}')
            old.with_name('sync.sh').write_text('#!/bin/sh\n')
            (mount / 'documents').mkdir()
            manager = SimpleNamespace(is_device_present=True,
                connected_device=SimpleNamespace(_main_prefix=tmp))
            resources = {'runtime/' + name: (ROOT / 'device' / name).read_bytes() for name in FILES}
            backup = install(manager, resources)
            self.assertIn(('/mnt/us/kc-sync/runtime/'+VERSION+'/run.sh').encode(), old.read_bytes())
            repeated = install(manager, resources)
            self.assertEqual(rollback(manager, repeated), 0)
            self.assertEqual(rollback(manager, backup), 4)
            self.assertEqual(old.read_bytes(), body)

    def test_fallback_install_and_rollback(self):
        mount = self.volume('fallback')
        old = mount / 'kmc/kpm/packages/kc/launch.sh'
        body = b'#!/bin/sh\r\nexec sh ./sync.sh\r\n'
        old.write_bytes(body)
        resources = {'runtime/' + name: (ROOT / 'device' / name).read_bytes() for name in FILES}
        with patch('plugin.mounts.windows_volumes', return_value=[(mount, 'Kindle')]):
            backup = install(self.manager(), resources)
            self.assertIn(('/runtime/'+VERSION+'/run.sh').encode(), old.read_bytes())
            self.assertEqual(rollback(self.manager(), backup), 4)
            self.assertEqual(old.read_bytes(), body)

    def test_runtime_upgrade_preserves_previous_version_and_pending(self):
        mount = self.volume('upgrade')
        old_runtime = mount / 'kc-sync/runtime/0.6.0/run.sh'
        old_runtime.parent.mkdir(parents=True)
        old_runtime.write_bytes(b'previous runtime retained')
        pending = mount / 'kc-sync/state/pending.json'
        pending.parent.mkdir()
        pending.write_bytes(b'{"retained":"unfinished task"}')
        old = mount / 'kmc/kpm/packages/kc/launch.sh'
        body = b'#!/bin/sh\nexec sh /mnt/us/kc-sync/runtime/0.6.0/run.sh\n'
        old.write_bytes(body)
        resources = {'runtime/' + name: (ROOT / 'device' / name).read_bytes() for name in FILES}
        backup = install(self.manager(mount), resources)
        self.assertIn(('/runtime/'+VERSION+'/run.sh').encode(), old.read_bytes())
        self.assertEqual(old_runtime.read_bytes(), b'previous runtime retained')
        self.assertEqual(pending.read_bytes(), b'{"retained":"unfinished task"}')
        self.assertEqual(rollback(self.manager(mount), backup), 4)
        self.assertEqual(old.read_bytes(), body)


if __name__ == '__main__':
    started = time.perf_counter()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(InstallDetection))
    (ROOT / 'install-detection-results.json').write_text(json.dumps(dict(
        passed=result.wasSuccessful(), tests=result.testsRun, failures=len(result.failures),
        errors=len(result.errors), seconds=time.perf_counter()-started, real_device_writes=0), indent=2))
    raise SystemExit(0 if result.wasSuccessful() else 1)
