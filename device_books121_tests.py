"""Independent discovery and explicit links using isolated real Calibre libraries."""
import copy,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from calibre.db.legacy import LibraryDatabase
from calibre.ebooks.metadata.book.base import Metadata
from plugin import book_links
from plugin.metadata import read_metadata,mappings
from plugin.deferred import discover,pending
from plugin.protocol import Invalid,canonical
from tests import snapshot,mount_fixture,receipt

class IndependentFiles(unittest.TestCase):
    def test_usb_without_calibre_markers_skips_assets_epub_and_indexed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);s=snapshot();store=mount_fixture(root,s)
            for name in ('metadata.calibre','driveinfo.calibre'):
                p=root/name
                if p.exists():p.unlink()
            folder=root/'documents';folder.mkdir()
            (folder/'outside.mobi').write_bytes(b'outside')
            (folder/'source.epub').write_bytes(b'epub')
            (folder/'assets.sdr').mkdir();(folder/'assets.sdr/asset.kfx').write_bytes(b'asset')
            value,descriptors=discover(store,s)
            self.assertEqual(len(descriptors),1);self.assertIsNone(value['books'][-1]['calibre_uuid'])
            self.assertTrue(pending(value['books'][-1]['uuid']))
            self.assertEqual(discover(store,s),(value,descriptors))
            indexed=copy.deepcopy(s);indexed['books'].append(dict(value['books'][-1],uuid='real-native'))
            self.assertEqual(discover(store,indexed)[1],[])
            (folder/'outside.mobi').write_bytes(b'changed')
            self.assertNotEqual(discover(store,s)[1],descriptors)

    def test_mtp_independent_file_can_be_queued_with_v2(self):
        from mtp_tests import Driver,resources
        from plugin.mtp import MTPStore,install_mtp
        from plugin.deferred import extend_request
        from plugin.planner import intent
        from tests import make_request
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);s=snapshot();mount_fixture(root,s);(root/'documents').mkdir()
            (root/'kc-sync/state/mtp.json').write_bytes(canonical(dict(schema='kc-mtp/v1',device=s['device'])))
            (root/'documents/other-tool.azw3').write_bytes(b'other tool book')
            driver=Driver(root);manager=SimpleNamespace(is_device_present=True,connected_device=driver)
            install_mtp(manager,resources());driver.rescan();store=MTPStore(manager)
            store.transfer_record=lambda:root/'transfer.json'
            value=store.snapshot();books=store.new_books;self.assertEqual(len(books),1)
            req=extend_request(make_request(value,[intent('add_members','c2',dict(members=[books[0]['alias']]))]),books)
            store.send(req);self.assertEqual(req['schema'],'kc-edit-request/v2')

class LibraryLinks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.db=LibraryDatabase(str(self.root/'library'));self.api=self.db.new_api
        self.s=snapshot();self.s['mapping']['calibre_library_uuid']=str(self.db.library_id)
        for book in self.s['books']:book['calibre_uuid']=None;book['metadata_matches']=0
        self.bid=self.s['books'][0]['uuid'];self.record=self.api.create_book_entry(Metadata('设备书',['作者']))
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def mapping(self,s=None):return mappings(s or self.s,read_metadata(self.api,['tags'])['rows'])
    def test_persist_isolation_identity_and_unlink(self):
        before=copy.deepcopy(self.s);fingerprint=read_metadata(self.api,[])['fingerprint']
        book_links.bind(self.api,self.s,self.bid,self.record);uid=self.api.field_for('uuid',self.record)
        self.assertEqual(self.mapping(),{uid:[self.bid]});self.assertEqual(self.s,before)
        self.assertNotEqual(read_metadata(self.api,[])['fingerprint'],fingerprint)
        other=copy.deepcopy(self.s);other['device']['instance_id']='other';self.assertEqual(self.mapping(other),{})
        other=copy.deepcopy(self.s);other['books'][0]['location']+='-replacement';self.assertEqual(self.mapping(other),{})
        db2=LibraryDatabase(str(self.root/'other'))
        try:self.assertEqual(mappings(self.s,read_metadata(db2.new_api,[])['rows']),{})
        finally:db2.close()
        self.db.close();self.db=LibraryDatabase(str(self.root/'library'));self.api=self.db.new_api
        self.assertEqual(self.mapping(),{uid:[self.bid]})
        self.api.set_field('tags',{self.record:['保留']});book_links.unlink(self.api,self.s,self.bid)
        self.assertEqual(self.mapping(),{});self.assertEqual(self.api.field_for('tags',self.record),('保留',))
        self.assertIn(self.record,self.api.all_book_ids())
    def test_metadata_only_creation_and_pending_rejection(self):
        ident=book_links.create_record(self.api,self.s,self.bid,'新记录',['作者'])
        self.assertFalse(self.api.formats(ident));self.assertEqual(self.api.field_for('title',ident),'新记录')
        self.assertIn(self.bid,self.mapping()[self.api.field_for('uuid',ident)])
        with self.assertRaises(Invalid):book_links.create_record(self.api,self.s,self.bid,'重复',[])
        pending_s=copy.deepcopy(self.s);pending_s['books'][1]['uuid']='kc-new-pending'
        with self.assertRaises(Invalid):book_links.bind(self.api,pending_s,'kc-new-pending',self.record)
    def test_binding_drives_column_preview_and_receipt_backfill(self):
        from plugin.workflow import adopt_profile,prepare
        from plugin.column_io import receipt_plan
        from plugin.planner import intent
        from workflow_tests import job
        self.s['mapping'].update(stable=False,calibre_library_uuid=None)
        book_links.bind(self.api,self.s,self.bid,self.record)
        metadata=read_metadata(self.api,['tags']);uid=self.api.field_for('uuid',self.record)
        profile=adopt_profile(self.s,metadata,str(self.db.library_id),'tags')
        self.assertIn(self.bid,profile['column_baseline'][uid]['copies'])
        plan=prepare(self.s,metadata,profile,[intent('add_members','c2',dict(members=[self.bid]))])
        sent=job(plan,self.s);result=receipt(sent['request'])
        changes=receipt_plan(profile,sent,result,metadata)
        self.assertTrue(changes);self.assertEqual(changes[0]['id'],self.record)
    def test_conflicting_automatic_binding_is_not_overridden(self):
        other=self.api.create_book_entry(Metadata('同名'))
        self.s['books'][0].update(calibre_uuid=self.api.field_for('uuid',other),metadata_matches=1)
        with self.assertRaises(Invalid):book_links.bind(self.api,self.s,self.bid,self.record)

if __name__=='__main__':unittest.main()
