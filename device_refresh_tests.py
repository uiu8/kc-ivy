"""File deletion, format replacement and opt-in native recency regressions."""
import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from tests import snapshot, mount_fixture
from plugin.protocol import Invalid, canonical, validate, seal
from plugin.planner import intent, plan, request
from plugin.deferred import draft_anchors, draft_book_bindings
from plugin.file_state import device_file_job


class Refresh(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.s=snapshot()
        self.s['mapping']['calibre_library_uuid']='library'
        self.store=mount_fixture(self.root,self.s)
        self.old=self.root/self.s['books'][0]['location'][len('/mnt/us/'):]

    def test_library_deletion_keeps_device_but_physical_deletion_refreshes(self):
        (self.root/'metadata.calibre').write_bytes(canonical([]))
        self.assertEqual(self.store.snapshot(scan_new=False),self.s)
        original=(self.root/'kc-sync/snapshots/latest.json').read_bytes()
        self.old.unlink()
        value=self.store.snapshot(scan_new=False)
        self.assertNotIn('b0',{b['uuid'] for b in value['books']})
        self.assertFalse(any(r['book_uuid']=='b0' for r in value['relations']))
        self.assertEqual(value['generated_utc'],self.s['generated_utc'])
        self.assertEqual((self.root/'kc-sync/snapshots/latest.json').read_bytes(),original)

    def test_cloud_membership_not_removed_by_download_deletion(self):
        self.s['relations'][0]['sideloaded']=0
        mount_fixture(self.root,self.s);self.old.unlink()
        self.assertEqual(self.store.snapshot(scan_new=False),self.s)

    def test_draft_survives_intermediate_delete_and_native_refresh(self):
        ops=[intent('add_members','c2',dict(members=['b0']))]
        anchors=draft_anchors(self.s,None,ops);self.old.unlink()
        deleted=self.store.snapshot(scan_new=False)
        anchors=draft_anchors(deleted,anchors,ops)
        # Kindle may already have exported a snapshot without the old UUID.
        (self.root/'kc-sync/snapshots/latest.json').write_bytes(canonical(deleted))
        (self.root/'documents/replacement.kfx').write_bytes(b'replacement')
        (self.root/'metadata.calibre').write_bytes(canonical([
            dict(uuid='calibre-0',lpath='documents/replacement.kfx',title='same book')]))
        value=self.store.snapshot();mapping=draft_book_bindings(anchors,value,self.store)
        target=next(b for b in value['books'] if b['calibre_uuid']=='calibre-0')
        self.assertEqual(mapping,{'b0':target['uuid']})
        duplicate=copy.deepcopy(target);duplicate['uuid']='ambiguous'
        value['books'].append(duplicate)
        self.assertEqual(draft_book_bindings(anchors,value,self.store),{})
        value['books'].pop();value['mapping']['calibre_library_uuid']='other'
        self.assertEqual(draft_book_bindings(anchors,value,self.store),{})

    def test_device_job_filter_excludes_library_and_other_managers(self):
        class Manager:
            def _delete_books(self):pass
            def _upload_books(self):pass
            def delete_books(self):pass
        manager=Manager()
        for method,expected in [(manager._delete_books,True),(manager._upload_books,True),
                                (manager.delete_books,False),(Manager()._delete_books,False)]:
            self.assertEqual(device_file_job(SimpleNamespace(func=method),manager),expected)

    def test_selected_column_scope_follows_replacement_without_adopting_other_books(self):
        from plugin.workflow import adopt_profile,prepare
        from plugin.scope import expand
        from workflow_tests import meta
        metadata=meta();profile=adopt_profile(self.s,metadata,'library','#shelf',{'calibre-0':['b0']})
        current=copy.deepcopy(self.s)
        current['relations']=[r for r in current['relations'] if r['book_uuid']!='b0']
        for uid in ('replacement','replacement-again'):
            current['books'][0]['uuid']=uid
            profile,_=expand(current,metadata,profile,selected_only=True)
            self.assertEqual(set(profile['column_baseline']),{'calibre-0'})
            self.assertEqual(profile['column_baseline']['calibre-0']['copies'],[uid])
            ops=prepare(current,metadata,profile)['plan'].data['operations']
            self.assertTrue(any(o['kind']=='add_members' and o['args']['members']==[uid] for o in ops))

    def test_recency_default_off_and_runtime_gate(self):
        from plugin.workflow import adopt_profile,prepare
        from workflow_tests import meta
        p=adopt_profile(self.s,meta(),'library','#shelf')
        ops=[intent('rename_collection','c1',dict(name='changed'))]
        for flag in (True,False):
            p['settings']['touch_collections']=flag
            preview=prepare(self.s,meta(),p,ops)['plan']
            req=request(preview,self.s,preview.data['inputs_digest'])
            self.assertIs(req['touch_collections'],flag)
            with self.assertRaises(Invalid):self.store.require_runtime(req)
            bad=seal(dict(req,touch_collections='yes'),'request_digest')
            with self.assertRaises(Invalid):validate(bad)
        (self.root/'documents/KC执行收藏夹任务.sh').write_text('/mnt/us/kc-sync/runtime/0.6.16/run.sh')
        self.store.require_runtime(req)


class RecencyExecutor(unittest.TestCase):
    def setUp(self):
        from executor_tests import Fixture
        self.f=Fixture();self.addCleanup(self.f.close)
        db=sqlite3.connect(self.f.root/'cc.db')
        db.execute('ALTER TABLE Entries ADD COLUMN p_lastAccess INTEGER DEFAULT 10')
        db.commit();db.close()

    def send(self,flag=True):
        p=plan(self.f.s,[intent('add_members','c2',dict(members=['b0']))],['c2'],'library',touch_collections=flag)
        return self.f.write(request(p,self.f.s,p.data['inputs_digest']))

    def test_recency_changes_only_target_and_never_reposts(self):
        req=self.send();result=self.f.run()
        self.assertEqual(result.returncode,0,result.stderr.decode())
        self.assertEqual(self.f.result(req)['operations'][0]['status'],'confirmed')
        self.assertGreater(self.f.query("SELECT p_lastAccess FROM Entries WHERE p_uuid='c2'")[0][0],10)
        self.assertEqual(self.f.query("SELECT p_lastAccess FROM Entries WHERE p_uuid='c1'"),[(10,)])
        count=len(self.f.posts());self.assertEqual(self.f.run().returncode,0)
        self.assertEqual(len(self.f.posts()),count)

    def test_off_preserves_existing_time(self):
        req=self.send(False);self.assertEqual(self.f.run().returncode,0)
        self.assertEqual(self.f.result(req)['operations'][0]['status'],'confirmed')
        self.assertEqual(self.f.query("SELECT p_lastAccess FROM Entries WHERE p_uuid='c2'"),[(10,)])

    def test_partial_recency_failure_stays_pending_without_replay(self):
        (self.f.root/'options.json').write_bytes(canonical(dict(omit_touch=True)))
        req=self.send();self.assertEqual(self.f.run().returncode,4)
        self.assertEqual(self.f.result(req)['operations'][0]['status'],'pending')
        count=len(self.f.posts());self.assertEqual(self.f.run().returncode,4)
        self.assertEqual(len(self.f.posts()),count)

    def test_deleted_original_then_new_format_uses_fresh_native_identity(self):
        from plugin.transport import DeviceStore
        from plugin.deferred import extend_request
        old=copy.deepcopy(self.f.s)
        for b in old['books']:b['location']='/mnt/us/documents/'+Path(b['location']).name
        (self.f.mount/'kc-sync/snapshots/latest.json').write_bytes(canonical(old))
        (self.f.mount/'documents/b0.txt').unlink()
        newfile=self.f.mount/'documents/replacement.kfx';newfile.write_bytes(b'new format')
        (self.f.mount/'metadata.calibre').write_bytes(canonical([
            dict(uuid='calibre-0',lpath='documents/replacement.kfx')]))
        db=sqlite3.connect(self.f.root/'cc.db')
        db.execute("DELETE FROM Collections WHERE i_member_uuid='b0'")
        db.execute("DELETE FROM Entries WHERE p_uuid='b0'")
        db.execute("INSERT INTO Entries(p_uuid,p_type,p_location,p_cdeKey,p_cdeType,p_collectionCount) VALUES('replacement','Entry:Item',?,'newkey','EBOK',0)",(newfile.as_posix(),))
        db.commit();db.close()
        store=DeviceStore(self.f.mount);current=store.snapshot()
        alias=store.new_books[0]['alias']
        p=plan(current,[intent('add_members','c1',dict(members=[alias]))],['c1'],'library',touch_collections=True)
        req=extend_request(request(p,current,p.data['inputs_digest']),store.new_books)
        # The simulator's database and shell use a temporary mount, never /mnt/us.
        req['new_books'][0]['location']=newfile.as_posix();req=seal(req,'request_digest')
        validator=self.f.device/'validate-request.sql'
        validator.write_text(validator.read_text(encoding='utf8').replace('/mnt/us/documents/',self.f.mount.as_posix()+'/documents/'),encoding='utf8')
        self.f.write(req);self.assertEqual(self.f.run().returncode,0,self.f.logs())
        from plugin.protocol import loads
        result=validate(loads((self.f.mount/'kc-sync/results'/(req['job_id']+'.json')).read_bytes()))
        self.assertEqual(result['operations'][0]['status'],'confirmed',result)
        self.assertEqual(result['operations'][0]['observed_digest'],req['operations'][0]['after'])
        self.assertEqual(self.f.query("SELECT i_member_uuid FROM Collections WHERE i_collection_uuid='c1' ORDER BY 1"),[('b1',),('replacement',)])


if __name__=='__main__':unittest.main()
