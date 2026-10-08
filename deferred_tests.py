import hashlib,tempfile,unittest,sqlite3
from pathlib import Path
from tests import snapshot,mount_fixture,make_request
from plugin.protocol import canonical,seal,validate,Invalid,loads
from plugin.deferred import discover
from executor_tests import Fixture

class Discovery(unittest.TestCase):
    def test_discovery_and_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);s=snapshot();store=mount_fixture(root,s)
            (root/'driveinfo.calibre').write_bytes(canonical(dict(device_store_uuid='store-1',last_library_uuid='library')))
            (root/'documents').mkdir();book=root/'documents/new.azw3';book.write_bytes(b'new book')
            (root/'metadata.calibre').write_bytes(canonical([dict(uuid='new-calibre',lpath='documents/new.azw3',title='New book')]))
            value,descriptors=discover(store,s)
            self.assertEqual(len(value['books']),4);self.assertEqual(value['books'][-1]['calibre_uuid'],'new-calibre')
            self.assertEqual(discover(store,s),(value,descriptors))
            # Real Calibre state: last_library_uuid can be null during USB use.
            s['mapping']['calibre_library_uuid']='library'
            for marker in (None,'missing'):
                drive=dict(device_store_uuid='store-1')
                if marker is None:drive['last_library_uuid']=None
                (root/'driveinfo.calibre').write_bytes(canonical(drive))
                recovered,found=discover(store,s)
                self.assertEqual(found,descriptors)
                self.assertEqual(recovered['books'][-1]['calibre_uuid'],'new-calibre')
            (root/'driveinfo.calibre').write_bytes(canonical(dict(device_store_uuid='store-1',last_library_uuid='other-library')))
            foreign,found=discover(store,s)
            self.assertEqual(len(found),1)
            self.assertIsNone(foreign['books'][-1]['calibre_uuid'])
            (root/'driveinfo.calibre').write_bytes(canonical(dict(device_store_uuid='store-1',last_library_uuid='library')))
            book.write_bytes(b'changed');self.assertNotEqual(discover(store,s)[1][0]['alias'],descriptors[0]['alias'])
    def test_unindexed_epub_is_not_a_pending_book(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);s=snapshot();store=mount_fixture(root,s)
            (root/'documents').mkdir();(root/'documents/source.epub').write_bytes(b'epub')
            (root/'metadata.calibre').write_bytes(canonical([dict(uuid='epub-source',lpath='documents/source.epub',title='Source')]))
            value,descriptors=discover(store,s)
            self.assertEqual(value,s);self.assertEqual(descriptors,[])
            self.assertTrue((root/'documents/source.epub').exists())

    def test_v2_digest_is_checked(self):
        req=make_request();req.update(schema='kc-edit-request/v2',new_books=[dict(alias='kc-new-'+'a'*64,location='/mnt/us/documents/a.azw3',size=1,sha256='a'*64)])
        req=seal(req,'request_digest');validate(req);req['new_books'][0]['size']=2
        with self.assertRaises(Invalid):validate(req)

class DeferredExecutor(unittest.TestCase):
    def setUp(self):
        self.f=Fixture();self.alias='kc-new-'+'a'*64
        p=self.f.device/'validate-request.sql'
        p.write_text(p.read_text(encoding='utf8').replace('/mnt/us/documents/',self.f.mount.as_posix()+'/documents/'),encoding='utf8')
        book=self.f.s['books'][2];self.real=book['uuid'];self.path=Path(book['location'])
        self.descriptor=dict(alias=self.alias,location=book['location'],size=self.path.stat().st_size,sha256=hashlib.sha256(self.path.read_bytes()).hexdigest())
        book['uuid']=self.alias
    def tearDown(self):self.f.close()
    def task(self):
        from plugin.planner import intent
        req=make_request(self.f.s,[intent('add_members','c2',dict(members=[self.alias]))])
        return self.f.write(seal(dict(req,schema='kc-edit-request/v2',new_books=[self.descriptor]),'request_digest'))
    def read_result(self,req):return loads((self.f.mount/'kc-sync/results'/(req['job_id']+'.json')).read_bytes())
    def test_resolve_native_uuid_and_no_repost(self):
        req=self.task();run=self.f.run();self.assertEqual(run.returncode,0,self.f.logs())
        result=self.read_result(req);self.assertEqual(result['book_bindings'],[dict(alias=self.alias,uuid=self.real)])
        self.assertTrue(all(o['status']=='confirmed' for o in result['operations']),result)
        self.assertEqual(result['operations'][0]['observed_digest'],req['operations'][0]['after'])
        self.assertEqual(self.f.query("SELECT i_member_uuid FROM Collections WHERE i_collection_uuid='c2'"),[(self.real,)])
        count=len(self.f.posts());self.assertEqual(self.f.run().returncode,0);self.assertEqual(len(self.f.posts()),count)
    def test_changed_file_never_posts(self):
        req=self.task();self.path.write_bytes(b'changed');self.assertEqual(self.f.run().returncode,0,self.f.logs())
        result=self.read_result(req);self.assertEqual(result['book_bindings'],[]);self.assertEqual(result['operations'][0]['status'],'conflict');self.assertEqual(self.f.posts(),[])
    def test_not_indexed_never_posts(self):
        req=self.task()
        db=sqlite3.connect(self.f.root/'cc.db');db.execute('DELETE FROM Entries WHERE p_uuid=?',(self.real,));db.commit();db.close()
        self.assertEqual(self.f.run().returncode,0,self.f.logs());self.assertEqual(self.read_result(req)['operations'][0]['status'],'conflict');self.assertEqual(self.f.posts(),[])
    def test_pending_readback_never_reposts(self):
        (self.f.root/'options.json').write_bytes(canonical(dict(omit_counts=True)))
        req=self.task();self.assertEqual(self.f.run().returncode,4,self.f.logs())
        self.assertEqual(self.read_result(req)['operations'][0]['status'],'pending')
        count=len(self.f.posts())
        db=sqlite3.connect(self.f.root/'cc.db');db.execute("UPDATE Entries SET p_collectionCount=1 WHERE p_uuid='b2'");db.commit();db.close()
        self.assertEqual(self.f.run().returncode,0,self.f.logs())
        self.assertEqual(len(self.f.posts()),count)
        self.assertEqual(self.read_result(req)['operations'][0]['status'],'confirmed')

class Roundtrip(unittest.TestCase):
    def test_alias_confirmed_backfills_and_next_preview_is_empty(self):
        from workflow_tests import meta,job
        from plugin.workflow import adopt_profile,prepare,apply_effects
        from plugin.planner import intent
        from plugin.deferred import extend_request
        from plugin.column_io import receipt_plan,accept_backfill
        from tests import receipt
        for field in ('#shelf','tags'):
            with self.subTest(field=field):
                s=snapshot();alias='kc-new-'+'b'*64;s['books'][2]['uuid']=alias
                m=meta();p=adopt_profile(s,m,'library',field)
                value=prepare(s,m,p,[intent('add_members','c2',dict(members=[alias]))]);j=job(value,s)
                j['request']=extend_request(j['request'],[dict(alias=alias,location=s['books'][2]['location'],size=1,sha256='b'*64)])
                result=seal(dict(receipt(j['request']),schema='kc-edit-result/v2',book_bindings=[dict(alias=alias,uuid='b2')]),'result_digest')
                j['result']=result;p=apply_effects(p,j,j['request']['operations'])
                self.assertEqual(p['column_baseline']['calibre-2']['copies'],['b2'])
                rows=receipt_plan(p,j,result,m);self.assertEqual(rows[0]['after'],['文学'])
                p=accept_backfill(p,rows);m['rows'][2]['fields'][field]=['文学']
                s['books'][2]['uuid']='b2';s['books'][2]['collection_count']=1
                s['relations'].append(dict(collection_uuid='c2',book_uuid='b2',member_type='EBOK',member_key='key2',member_present=1,sideloaded=1,order=0))
                again=prepare(s,m,p,[])
                self.assertFalse(again['plan'].data['operations'],again)
class CalibreNewBook(unittest.TestCase):
    def test_usb_submit_receive_and_real_column_write(self):
        from calibre.db.legacy import LibraryDatabase
        from calibre.ebooks.metadata.book.base import Metadata
        from plugin.service import Service
        from plugin.planner import intent
        from tests import receipt
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db=LibraryDatabase(str(root/'library'))
            try:
                api=db.new_api;i=api.create_book_entry(Metadata('新书'));uid=api.field_for('uuid',i);library=str(db.library_id)
                s=snapshot();s['books']=[];s['relations']=[];s['mapping']['calibre_library_uuid']=library
                mount=root/'mount';mount.mkdir();store=mount_fixture(mount,s)
                (mount/'driveinfo.calibre').write_bytes(canonical(dict(device_store_uuid='store-1',last_library_uuid=library)))
                (mount/'documents').mkdir();(mount/'documents/new.azw3').write_bytes(b'book')
                launcher=mount/'documents/KC执行收藏夹任务.sh';launcher.write_text('#!/bin/sh\nexec sh /mnt/us/kc-sync/runtime/0.6.14/run.sh')
                (mount/'metadata.calibre').write_bytes(canonical([dict(uuid=uid,lpath='documents/new.azw3',title='新书')]))
                current=store.snapshot();alias=current['books'][0]['uuid'];svc=Service(root/'private')
                svc.adopt(api,library,current,'tags',{uid:[alias]});manual=[intent('add_members','c2',dict(members=[alias]))]
                value=svc.preview(api,library,current,manual);jid=svc.submit(api,library,store,value,manual);j=svc.state.job(jid)
                self.assertEqual(j['request']['schema'],'kc-edit-request/v2')
                self.assertEqual(loads(store.path('inbox/'+jid+'.json').read_bytes()),j['request'])
                result=seal(dict(receipt(j['request']),schema='kc-edit-result/v2',book_bindings=[dict(alias=alias,uuid='real-book')]),'result_digest')
                current['books'][0]['uuid']='real-book';current['books'][0]['collection_count']=1
                current['relations']=[dict(collection_uuid='c2',book_uuid='real-book',member_type=None,member_key=None,member_present=1,sideloaded=1,order=0)]
                svc.receive(library,current,[result]);changed,notes=svc.backfill_confirmed(api,library,current)
                self.assertEqual(changed,[i],notes);self.assertEqual(api.field_for('tags',i),('文学',))
                self.assertFalse(svc.preview(api,library,current,[])['plan'].data['operations'])
            finally:db.close()

