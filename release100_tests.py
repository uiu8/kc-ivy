import tempfile,unittest,os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from plugin.install import install,rollback,FILES
from plugin.protocol import Invalid
from plugin.sharing import ensure_import_space

class ReleaseRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        for n in ('documents','system','audible'):(self.root/n).mkdir()
        self.manager=SimpleNamespace(is_device_present=True,connected_device=SimpleNamespace(_main_prefix=str(self.root),name='Kindle'))
        self.resources={'runtime/'+n:b'fixture' for n in FILES}
    def test_delete_interrupt_and_repeat(self):
        backup=install(self.manager,self.resources);unlink=Path.unlink
        def crash(p,*a,**kw):unlink(p,*a,**kw);raise OSError('disconnected')
        with patch.object(Path,'unlink',crash),self.assertRaises(OSError):rollback(self.manager,backup)
        self.assertEqual(rollback(self.manager,backup),2)
        self.assertEqual(rollback(self.manager,backup),0)
    def test_replace_interrupt_and_repeat(self):
        target=self.root/'documents/KC刷新收藏夹.sh';target.write_bytes(b'original')
        backup=install(self.manager,self.resources);replace=os.replace
        def crash(a,b):replace(a,b);raise OSError('disconnected')
        with patch('plugin.install.os.replace',crash),self.assertRaises(OSError):rollback(self.manager,backup)
        self.assertEqual(rollback(self.manager,backup),2)
        self.assertEqual(target.read_bytes(),b'original')
    def test_partial_restore_resumes_and_foreign_data_blocks(self):
        target=self.root/'documents/KC刷新收藏夹.sh';target.write_bytes(b'original')
        backup=install(self.manager,self.resources)
        partial=target.with_name(target.name+'.kc-restore');partial.write_bytes(b'foreign')
        with self.assertRaises(Invalid):rollback(self.manager,backup)
        partial.write_bytes(b'ori')
        self.assertEqual(rollback(self.manager,backup),3)
        self.assertEqual(target.read_bytes(),b'original')
    def test_modified_entry_blocks_before_other_changes(self):
        backup=install(self.manager,self.resources)
        target=self.root/'documents/KC执行收藏夹任务.sh';target.write_bytes(b'user edit')
        with self.assertRaises(Invalid):rollback(self.manager,backup)
        self.assertEqual(len(list((self.root/'documents').glob('*.sh'))),3)
        self.assertEqual(target.read_bytes(),b'user edit')
    def test_space_failure_is_clear(self):
        with patch('plugin.sharing.shutil.disk_usage',return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(Invalid,'空间不足'):ensure_import_space(self.root,[dict(size=1024)])
            ensure_import_space(self.root,[])
