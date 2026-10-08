import unittest,tempfile
from pathlib import Path
from tests import snapshot,mount_fixture
from plugin.protocol import canonical,digest
from plugin.service import Service
from plugin.deferred import PREFIX
class Repair(unittest.TestCase):
 def test_identity_marker_transition_and_legacy_repair(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);mount=root/'device';mount.mkdir();s=snapshot();store=mount_fixture(mount,s)
   (mount/'documents').mkdir();(mount/'documents/new.azw3').write_bytes(b'book')
   (mount/'metadata.calibre').write_bytes(canonical([dict(uuid='calibre-new',lpath='documents/new.azw3',title='Book')]))
   drive=mount/'driveinfo.calibre'
   drive.write_bytes(canonical(dict(device_store_uuid='store-1',last_library_uuid='library')))
   a=store.snapshot();desc=store.new_books[0];new=desc['alias']
   drive.write_bytes(canonical(dict(device_store_uuid='store-1',last_library_uuid=None)))
   b=store.snapshot();self.assertEqual(a['books'],b['books'])
   svc=Service(root/'state');p=svc.profile('library',b);key=svc.key('library',b)
   old=PREFIX+digest(['library','calibre-new',desc['location'],desc['sha256']])
   p['column_baseline']={'calibre-new':dict(id=1,copies=[old],values=['架'],pending_copies={old:['架']})}
   svc.state.save(key,p,p['revision'])
   svc.state.save_draft(key,dict(intents=[dict(kind='add_members',args=dict(members=[old]))],snapshot_digest='old',resolutions={},issues=[]))
   self.assertTrue(svc.state.reconcile_pending(key,b,store.new_books))
   base=svc.state.get(key)['column_baseline']['calibre-new']
   self.assertEqual(base['copies'],[new]);self.assertEqual(base['pending_copies'],{new:['架']})
   self.assertEqual(svc.state.draft(key)['intents'][0]['args']['members'],[new])
   self.assertFalse(svc.state.reconcile_pending(key,b,store.new_books))
   with svc.state.connect() as db:self.assertTrue(db.execute('select count(*) from history').fetchone()[0])
 def test_changed_fingerprint_does_not_rebind(self):
  with tempfile.TemporaryDirectory() as tmp:
   svc=Service(tmp);s=snapshot();p=svc.profile('library',s);key=svc.key('library',s)
   p['column_baseline']={'book':dict(copies=['kc-new-'+'a'*64],values=[])}
   svc.state.save(key,p,p['revision'])
   self.assertFalse(svc.state.reconcile_pending(key,s,[]))
   self.assertEqual(svc.state.get(key)['column_baseline'],p['column_baseline'])
