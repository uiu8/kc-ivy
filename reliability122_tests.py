import copy,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from tests import snapshot,mount_fixture,make_request,receipt
from plugin.protocol import canonical,Cancelled,cancellable,reporting,digest,Invalid
from plugin.deferred import draft_book_bindings,PREFIX
from plugin.readiness import book_status,attention_jobs,library_context
from device_books121_tests import LibraryLinks

class DiscoveryLifecycle(unittest.TestCase):
    def test_mtp_driver_progress_callback_honors_cancel(self):
        from mtp_tests import Driver
        from plugin.mtp import MTPStore
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);s=snapshot();mount_fixture(root,s);(root/'documents').mkdir()
            (root/'documents/new.mobi').write_bytes(b'book')
            driver=Driver(root);original=driver.get_mtp_file_by_name;event=threading.Event();messages=[]
            def transfer(parent,*names,stream=None,callback=None):
                if stream is not None:
                    callback(0,4);event.set();callback(1,4)
                return original(parent,*names,stream=stream,callback=callback)
            driver.get_mtp_file_by_name=transfer
            store=MTPStore(SimpleNamespace(is_device_present=True,connected_device=driver))
            with cancellable(event),reporting(messages.append),self.assertRaises(Cancelled):store.snapshot()
            self.assertTrue(any('MTP 正在读取' in m for m in messages))
            self.assertEqual((root/'documents/new.mobi').read_bytes(),b'book')
    def test_identity_survives_calibre_mapping_and_native_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);s=snapshot();store=mount_fixture(root,s);(root/'documents').mkdir()
            path=root/'documents/outside.azw3';path.write_bytes(b'content')
            old=store.snapshot();uid=old['books'][-1]['uuid'];descriptor=store.new_books[0]
            (root/'metadata.calibre').write_bytes(canonical([dict(uuid='calibre-new',lpath='documents/outside.azw3',title='New title')]))
            new=store.snapshot();self.assertEqual(new['books'][-1]['uuid'],uid)
            legacy=copy.deepcopy(old);legacy['books'][-1]['uuid']=PREFIX+digest([old['device']['instance_id'],None,descriptor['location'],descriptor['sha256']])
            self.assertEqual(draft_book_bindings(legacy,new,store),{legacy['books'][-1]['uuid']:uid})
            native=copy.deepcopy(new);native['books'][-1]['uuid']='native-book';store.new_books=[]
            self.assertEqual(draft_book_bindings(old,native,store),{uid:'native-book'})
            path.write_bytes(b'changed');self.assertEqual(draft_book_bindings(old,native,store),{})

    def test_fast_read_and_cancel_do_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);s=snapshot();store=mount_fixture(root,s);(root/'documents').mkdir()
            (root/'documents/new.mobi').write_bytes(b'book')
            self.assertEqual(store.snapshot(scan_new=False),s);self.assertEqual(store.new_books,[])
            before={p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            event=threading.Event();messages=[]
            def progress(message):messages.append(message);event.set()
            with cancellable(event),reporting(progress),self.assertRaises(Cancelled):store.snapshot()
            self.assertTrue(messages)
            self.assertEqual(before,{p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_large_candidate_list_and_epub_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);s=snapshot();store=mount_fixture(root,s);folder=root/'documents';folder.mkdir()
            for i in range(1000):(folder/f'{i}.mobi').write_bytes(str(i).encode())
            (folder/'not-indexed.epub').write_bytes(b'epub')
            value=store.snapshot();self.assertEqual(len(store.new_books),1000);self.assertEqual(len(value['books']),1003)

class RepairAndSharing(LibraryLinks):
    def test_select_one_device_copy_keeps_other_out_of_column_changes(self):
        from plugin import book_links
        from plugin.metadata import read_metadata
        from plugin.service import Service
        book_links.bind(self.api,self.s,self.bid,self.record)
        other=self.s['books'][1]['uuid'];book_links.bind(self.api,self.s,other,self.record)
        library=str(self.db.library_id);service=Service(self.root/'state');profile=service.profile(library,self.s)
        metadata=read_metadata(self.api,['tags']);uid=self.api.field_for('uuid',self.record)
        configured=service.configure(self.api,library,self.s,'tags',{uid:[self.bid]},[],profile['settings'],profile['revision'],metadata['fingerprint'],scope_mode='selected')
        self.assertEqual(configured['column_baseline'][uid]['copies'],[self.bid])
        self.api.set_field('tags',{self.record:['副本测试架']})
        plan=service.preview(self.api,library,self.s,[])
        members={bid for op in plan['plan'].data['operations'] for bid in op['args'].get('members',[])}
        self.assertIn(self.bid,members);self.assertNotIn(other,members)
    def test_stale_binding_repair_keeps_record_and_values(self):
        from plugin import book_links
        book_links.bind(self.api,self.s,self.bid,self.record)
        self.api.set_field('tags',{self.record:['保留']})
        changed=copy.deepcopy(self.s);changed['books'][0]['uuid']='replacement'
        self.assertEqual(book_links.link_report(self.api,changed)[0]['reason'],'设备编号不在当前快照')
        book_links.repair(self.api,changed,self.bid,'replacement')
        self.assertEqual(book_links.link_report(self.api,changed)[0]['reason'],'')
        self.assertEqual(self.api.field_for('tags',self.record),('保留',))
        self.assertNotIn(self.bid,book_links.links(self.api)[book_links.device_key(changed)])

    def test_share_inventory_explains_skip_and_blocks_stale_plan(self):
        from plugin.sharing import share_inventory,export_share
        from calibre.ebooks.metadata.book.base import Metadata
        from io import BytesIO
        other=self.api.create_book_entry(Metadata('有文件'));self.api.add_format(other,'TXT',BytesIO(b'book'))
        inventory=share_inventory(self.api,[self.record,other],['TXT'])
        self.assertIn('仅元数据',inventory['rows'][0]['status']);self.assertEqual(inventory['rows'][1]['formats'],['TXT'])
        old=share_inventory(self.api,[other],['MOBI'])
        self.assertIn('格式不存在',old['rows'][0]['status'])
        self.api.set_field('title',{other:'Changed'})
        with self.assertRaises(Invalid):export_share(self.api,str(self.db.library_id),[self.record,other],'tags',self.root/'share.zip',['TXT'],expected_inventory=inventory['fingerprint'])
        self.assertFalse((self.root/'share.zip').exists())

class Readiness(unittest.TestCase):
    def test_stages_and_old_uncertain_task_badge(self):
        book=dict(uuid='kc-new-test');req=make_request();req['new_books']=[dict(alias=book['uuid'])]
        req['operations'][0]['args']['members']=[book['uuid']]
        job=dict(request=req,status='staged');self.assertIn('等待',book_status(book,[job])[0])
        result=receipt(req);result['operations'][0]['status']='conflict';job.update(result=result,status='closed')
        self.assertIn('未能匹配',book_status(book,[job])[0]);self.assertEqual(attention_jobs([job],None),[])
        result['operations'][0]['status']='pending';self.assertEqual(len(attention_jobs([job],None)),1)
        self.assertIn('待核验',book_status(book,[job])[0])
        result['book_bindings']=[dict(alias=book['uuid'],uuid='real')];self.assertIn('已匹配',book_status(book,[job])[0])
        self.assertIn('已有原生编号',book_status(dict(uuid='native'),[])[0])
    def test_foreign_library_guidance(self):
        s=snapshot();s['mapping']['calibre_library_uuid']='old-library'
        text=library_context(s,'new-library');self.assertIn('old-library',text);self.assertIn('new-library',text);self.assertIn('暂停列同步',text)
