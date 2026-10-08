import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from plugin.runtime_versions import preview,cleanup
from plugin.protocol import Invalid,digest
from plugin.install import rollback

class Versions(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.mount=Path(self.tmp.name);self.root=self.mount/'kc-sync'
  (self.root/'state').mkdir(parents=True);(self.mount/'documents').mkdir()
  self.store=SimpleNamespace(root=self.root,mount=self.mount,check_identity=lambda:None,path=lambda s:self.root/s)
  self.entry=self.mount/'documents/KC执行收藏夹任务.sh'
  for i in range(1,6):
   d=self.root/'runtime'/f'0.6.{i}';d.mkdir(parents=True)
   (d/'run.sh').write_text('runtime '+str(i));(d/'refresh.sh').write_text('refresh')
   if i>1:
    b=self.root/'upgrades'/str(i);b.mkdir(parents=True)
    (b/'0.bak').write_text(self.launch(i-1),encoding='utf8',newline='\n')
    (b/'manifest.json').write_text(json.dumps(dict(version=f'0.6.{i}',entries=[dict(path='documents/KC执行收藏夹任务.sh',backup='0.bak',after=digest(self.launch(i)))])),encoding='utf8')
  self.entry.write_text(self.launch(5),encoding='utf8',newline='\n')
  (self.root/'inbox').mkdir();(self.root/'inbox/keep.json').write_text('user task')
 def launch(self,n):return f'#!/bin/sh\nexec sh /mnt/us/kc-sync/runtime/0.6.{n}/run.sh\n'
 def test_cleanup_retains_two_rollback_steps_and_task(self):
  plan=preview(self.store);self.assertFalse(plan['blocked']);self.assertEqual(cleanup(self.store,plan),2)
  self.assertEqual(sorted(p.name for p in (self.root/'runtime').iterdir()),['0.6.3','0.6.4','0.6.5'])
  self.assertEqual(sorted(p.name for p in (self.root/'upgrades').iterdir()),['4','5'])
  with patch('plugin.install.resolve_mount',return_value=self.mount):
   rollback(None,self.root/'upgrades/5');self.assertEqual(self.entry.read_text(),self.launch(4))
   rollback(None,self.root/'upgrades/4');self.assertEqual(self.entry.read_text(),self.launch(3))
  self.assertEqual((self.root/'inbox/keep.json').read_text(),'user task')
 def test_installer_one_based_records(self):
  for folder in (self.root/'upgrades').iterdir():
   entry=json.loads((folder/'manifest.json').read_text())['entries'][0]
   (folder/'1.record.json').write_text(json.dumps(entry),encoding='utf8')
  plan=preview(self.store);self.assertFalse(plan['blocked'])
  self.assertEqual(cleanup(self.store,plan),2)
 def test_record_mismatch_blocks(self):
  (self.root/'upgrades/5/1.record.json').write_text('{}')
  self.assertTrue(preview(self.store)['blocked'])
 def test_changed_entry_refused(self):
  plan=preview(self.store);self.entry.write_text(self.launch(1))
  with self.assertRaises(Invalid):cleanup(self.store,plan)
  self.assertTrue((self.root/'runtime/0.6.1').exists())
 def test_all_active_entry_versions_retained(self):
  (self.mount/'documents/extra.sh').write_text(self.launch(1))
  plan=preview(self.store);self.assertFalse(next(r for r in plan['versions'] if r['version']=='0.6.1')['remove'])
 def test_unknown_backup_and_pending_block(self):
  (self.root/'upgrades/5/foreign.txt').write_text('keep')
  plan=preview(self.store);self.assertTrue(plan['blocked'])
  with self.assertRaises(Invalid):cleanup(self.store,plan)
  (self.root/'upgrades/5/foreign.txt').unlink();(self.root/'state/pending.json').write_text('{}')
  self.assertTrue(preview(self.store)['blocked'])
 def test_unknown_runtime_file_preserved(self):
  (self.root/'runtime/0.6.1/foreign.txt').write_text('keep')
  cleanup(self.store,preview(self.store));self.assertTrue((self.root/'runtime/0.6.1/foreign.txt').exists())
 def test_interrupted_backup_cleanup_resumes(self):
  original=Path.unlink;target=self.root/'upgrades/2/0.bak'
  def fail(path,*args,**kwargs):
   if path==target:raise OSError('disconnect')
   return original(path,*args,**kwargs)
  with patch.object(Path,'unlink',fail):
   with self.assertRaises(OSError):cleanup(self.store,preview(self.store))
  self.assertFalse((self.root/'upgrades/2/manifest.json').exists())
  self.assertEqual(cleanup(self.store,preview(self.store)),2)
 def test_interrupted_runtime_cleanup_resumes(self):
  original=Path.unlink;target=self.root/'runtime/0.6.1/run.sh'
  def fail(path,*args,**kwargs):
   if path==target:raise OSError('disconnect')
   return original(path,*args,**kwargs)
  with patch.object(Path,'unlink',fail):
   with self.assertRaises(OSError):cleanup(self.store,preview(self.store))
  self.assertEqual(cleanup(self.store,preview(self.store)),2)
 def test_link_and_mtp_refused(self):
  self.store.experimental_mtp=True
  with self.assertRaises(Invalid):preview(self.store)
  self.store.experimental_mtp=False
  with patch.object(Path,'is_symlink',return_value=True):
   with self.assertRaises(Invalid):preview(self.store)
