"""MTP interface simulation + actual shell/SQLite executor; no physical device."""
import hashlib
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from calibre.devices.mtp.filesystem_cache import FilesystemCache
from plugin.mtp import MTPStore, MTPFiles, install_mtp,rollback_mtp,can_enable
from plugin.transport import connected_store
from plugin.protocol import Invalid, canonical, digest
from plugin.probe import prepare_probe
from plugin.install import FILES, VERSION
from plugin import policy
from tests import snapshot, make_request, mount_fixture, receipt

ROOT = Path(__file__).resolve().parent


class Driver:
    """Calibre's real filesystem objects, with physical USB I/O replaced by files."""
    is_kindle = True
    _main_id = 1
    def __init__(self, root):
        self.root = root; self.reads=[]; self.fail=None; self.serial=10
        self.rescan()
    def rescan(self):
        entries=[]; ids={self.root:1}
        for p in sorted(self.root.rglob('*'), key=lambda x:(len(x.parts),str(x))):
            self.serial+=1; ids[p]=self.serial
            entries.append(dict(id=ids[p],storage_id=1,parent_id=ids[p.parent],name=p.name,is_folder=p.is_dir(),size=p.stat().st_size))
        self.filesystem_cache=FilesystemCache([dict(id=1,name='Kindle',is_folder=True)],entries)
    def is_folder_ignored(self,*args): return False
    def get_mtp_file(self,f,stream=None,callback=None):
        return self.get_mtp_file_by_name(f.storage,*f.full_path[1:])
    def find_calibre_file_path(self,storage,key):return [key+'.calibre']
    def get_mtp_file_by_name(self,parent,*names,stream=None,callback=None):
        if stream is not None:
            import shutil
            self.reads.append('/'.join(names))
            with self.root.joinpath(*names).open('rb') as source:shutil.copyfileobj(source,stream,1024*1024)
            stream.seek(0);return stream
        self.reads.append('/'.join(names));return BytesIO(self.root.joinpath(*names).read_bytes())
    def create_folder(self,parent,name):
        old=parent.folder_named(name)
        if old:return old
        self.root.joinpath(*parent.full_path[1:],name).mkdir()
        self.serial+=1
        return parent.add_child(dict(id=self.serial,storage_id=1,parent_id=parent.object_id,name=name,is_folder=True))
    def put_file(self,parent,name,stream,size,callback=None,replace=True):
        old=parent.file_named(name)
        if old:
            if not replace:raise FileExistsError(name)
            parent.remove_child(old)
        data=stream.read();assert len(data)==size
        fail=self.fail and name.endswith(self.fail)
        if fail:data=data[:max(1,len(data)//2)];self.fail=None
        self.root.joinpath(*parent.full_path[1:],name).write_bytes(data)
        self.serial+=1
        node=parent.add_child(dict(id=self.serial,storage_id=1,parent_id=parent.object_id,name=name,is_folder=False,size=len(data)))
        if fail:raise OSError('simulated cable disconnect')
        return node

    def delete_file_or_folder(self,node):
        if node.is_folder:raise AssertionError('Rollback must not delete directories')
        self.root.joinpath(*node.full_path[1:]).unlink()
        node.parent.remove_child(node)


def resources():
    return {'runtime/'+n:(ROOT/'device'/n).read_bytes().replace(b'\r\n',b'\n') if n.endswith(('.sh','.sql')) else (ROOT/'device'/n).read_bytes() for n in FILES}


class MTP(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.mount=self.base/'device';self.mount.mkdir()
        self.s=snapshot();mount_fixture(self.mount,self.s);(self.mount/'documents').mkdir()
        (self.mount/'kc-sync/state/mtp.json').write_bytes(canonical(dict(schema='kc-mtp/v1',device=self.s['device'])))
        self.connect()
    def connect(self):
        self.driver=Driver(self.mount)
        self.manager=SimpleNamespace(is_device_present=True,connected_device=self.driver)
        self.store=MTPStore(self.manager)
        self.store.transfer_record=lambda:self.base/'transfer.json'
    def new_book(self, large=False):
        from calibre.devices.mtp.books import Book, BookList, JSONCodec
        path=self.mount/'documents/new.azw3'
        with path.open('wb') as out:
            out.write(b'new book')
            if large:out.truncate(65*1024*1024)
        books=BookList(1);book=Book(1,'documents/new.azw3');book.uuid='calibre-new';book.title='New book';books.append(book)
        with (self.mount/'metadata.calibre').open('wb') as out:JSONCodec().encode_to_file(out,books)
        install_mtp(self.manager,resources());self.connect()
        snap=self.store.snapshot()
        from plugin.deferred import extend_request
        from plugin.planner import intent
        req=extend_request(make_request(snap,[intent('add_members','c2',dict(members=[snap['books'][-1]['uuid']]))]),self.store.new_books)
        return path,snap,req

    def test_large_new_book_cache_send_and_recovery(self):
        from plugin.deferred import recovery_bindings
        path,snap,req=self.new_book(large=True)
        self.assertEqual(req['schema'],'kc-edit-request/v2')
        count=self.driver.reads.count('documents/new.azw3')
        self.assertEqual(self.store.snapshot(),snap)
        self.assertEqual(self.driver.reads.count('documents/new.azw3'),count)
        self.store.send(req)
        self.assertEqual(self.driver.reads.count('documents/new.azw3'),count+1)
        stage=self.mount/'kc-sync/mtp-inbox'/(req['job_id']+'.json')
        self.assertEqual(stage.read_bytes(),canonical(req))
        snap['books'][-1]['uuid']='real-new'
        self.assertEqual(recovery_bindings(req,snap,self.store,req['operations']),[dict(alias=req['new_books'][0]['alias'],uuid='real-new')])

    def test_driver_metadata_location_and_reconnect_cache(self):
        path,snap,req=self.new_book()
        folder=self.mount/'calibre';folder.mkdir()
        for name in ('metadata','driveinfo'):
            (self.mount/(name+'.calibre')).rename(folder/(name+'.calibre'))
        self.connect()
        self.driver.find_calibre_file_path=lambda storage,key:['calibre',key+'.calibre']
        self.assertEqual(self.store.snapshot(),snap)
        self.assertEqual(self.driver.reads.count('documents/new.azw3'),1)

    def test_same_size_changed_new_book_blocks_send(self):
        path,snap,req=self.new_book()
        path.write_bytes(b'bad book')
        with self.assertRaises(Invalid):self.store.send(req)
        self.assertFalse(list((self.mount/'kc-sync/mtp-inbox').glob('*.ready')))

    def test_old_runtime_and_v2_disconnect_retry(self):
        path,snap,req=self.new_book()
        entry=self.mount/'documents/KC执行收藏夹任务-MTP.sh';raw=entry.read_bytes()
        entry.write_bytes(raw.replace(b'0.6.15',b'0.6.14'))
        with self.assertRaises(Invalid):self.store.send(req)
        entry.write_bytes(raw)
        self.driver.fail='.ready'
        with self.assertRaises(OSError):self.store.send(req)
        self.connect();self.store.retry_transfer()
        stage=self.mount/'kc-sync/mtp-inbox'
        self.assertEqual((stage/(req['job_id']+'.json')).read_bytes(),canonical(req))

    def test_opt_in_and_other_device_rejected(self):
        with patch('plugin.mtp.enabled',return_value=False),self.assertRaises(Invalid):connected_store(self.manager)
        self.driver.is_kindle=False
        with self.assertRaises(Invalid):MTPStore(self.manager)
    def test_send_is_staged_and_repeat_is_idempotent(self):
        req=make_request(self.s);self.store.send(req);self.store.send(req)
        stage=self.mount/'kc-sync/mtp-inbox';raw=(stage/(req['job_id']+'.json')).read_bytes()
        self.assertEqual(raw,canonical(req))
        self.assertEqual((stage/(req['job_id']+'.ready')).read_text(),hashlib.sha256(raw).hexdigest()+' edit\n')
        self.assertFalse(list((self.mount/'kc-sync/inbox').iterdir()))
        self.assertFalse(any(p.startswith('documents/') for p in self.driver.reads))
        with self.assertRaises(Invalid):self.store.send(make_request(self.s))
    def test_interrupted_payload_and_marker_resume_exact_original(self):
        for suffix in ('.json','.ready'):
            with self.subTest(suffix=suffix):
                req=make_request(self.s);self.driver.fail=suffix
                with self.assertRaises(OSError):self.store.send(req)
                self.assertFalse(list((self.mount/'kc-sync/inbox').iterdir()))
                self.connect();self.store.retry_transfer()
                stage=self.mount/'kc-sync/mtp-inbox'
                self.assertEqual((stage/(req['job_id']+'.json')).read_bytes(),canonical(req))
                for f in stage.iterdir():f.unlink()
                self.connect()
    def test_conflicting_bytes_and_device_change_stop(self):
        req=make_request(self.s);self.store.send(req)
        path=self.mount/'kc-sync/mtp-inbox'/(req['job_id']+'.json');path.write_bytes(b'changed')
        with self.assertRaises(Invalid):self.store.send(req)
        self.driver.rescan()
        with self.assertRaises(Invalid):self.store.snapshot()
    def test_safety_checks_and_receipt(self):
        req=make_request(self.s)
        self.s['snapshot_id']='changed'
        (self.mount/'kc-sync/snapshots/latest.json').write_bytes(canonical(self.s))
        with self.assertRaises(Invalid):self.store.send(req)
        (self.mount/'kc-sync/inbox'/(req['job_id']+'.json')).write_bytes(canonical(req))
        result=receipt(req)
        (self.mount/'kc-sync/results'/(req['job_id']+'.json')).write_bytes(canonical(result))
        self.connect();self.assertEqual(self.store.receipts(),[result])
    def test_probe_policy_and_install(self):
        req=prepare_probe(self.s,'b2','library');self.store.send_probe(req)
        self.assertTrue((self.mount/'kc-sync/mtp-inbox'/(req['job_id']+'.ready')).read_text().endswith(' probe\n'))
        for p in (self.mount/'kc-sync/mtp-inbox').iterdir():p.unlink()
        self.connect();task=policy.build(self.s,['c1']);policy.send(self.store,task)
        self.assertTrue((self.mount/'kc-sync/mtp-inbox'/(task['job_id']+'.ready')).read_text().endswith(' policy\n'))
        resources={'runtime/'+n:(ROOT/'device'/n).read_bytes().replace(b'\r\n',b'\n') if n.endswith(('.sh','.sql')) else (ROOT/'device'/n).read_bytes() for n in FILES}
        original=self.mount/'documents/KC刷新收藏夹.sh';original.write_bytes(b'original')
        install_mtp(self.manager,resources);install_mtp(self.manager,resources)
        self.assertEqual(original.read_bytes(),b'original')
        self.assertEqual(len(list((self.mount/'documents').glob('*-MTP.sh'))),3)
        for name in FILES:self.assertEqual((self.mount/'kc-sync/runtime'/VERSION/name).read_bytes(),resources['runtime/'+name])
    def test_maintenance_is_explicitly_readonly(self):
        with self.assertRaises(Invalid):self.store.path('state/kcpp-transfer.lock').mkdir()

    def test_withdraw_preserves_data_and_reinstall(self):
        original=self.mount/'documents/KC执行收藏夹任务.sh';original.write_bytes(b'old launcher')
        install_mtp(self.manager,resources())
        before={str(p.relative_to(self.mount)):p.read_bytes() for p in self.mount.rglob('*') if p.is_file() and not p.name.endswith('-MTP.sh')}
        with patch('plugin.mtp.enabled',return_value=False):
            self.assertEqual(rollback_mtp(self.manager,resources()),3)
            self.assertEqual(rollback_mtp(self.manager,resources()),0)
        after={str(p.relative_to(self.mount)):p.read_bytes() for p in self.mount.rglob('*') if p.is_file()}
        self.assertEqual(before,after)
        install_mtp(self.manager,resources())
        self.assertEqual(len(list((self.mount/'documents').glob('*-MTP.sh'))),3)

    def test_withdraw_preflights_all_entries(self):
        install_mtp(self.manager,resources())
        paths=list((self.mount/'documents').glob('*-MTP.sh'));paths[-1].write_bytes(b'unrelated')
        with self.assertRaises(Invalid):rollback_mtp(self.manager,resources())
        self.assertTrue(all(p.exists() for p in paths))
        with self.assertRaises(Invalid):install_mtp(self.manager,resources())

    def test_withdraw_blocks_staged_and_unconfirmed_tasks(self):
        install_mtp(self.manager,resources());req=make_request(self.s);self.store.send(req)
        with self.assertRaises(Invalid):rollback_mtp(self.manager,resources())
        for p in (self.mount/'kc-sync/mtp-inbox').iterdir():p.unlink()
        (self.mount/'kc-sync/inbox'/(req['job_id']+'.json')).write_bytes(canonical(req));self.connect()
        with self.assertRaises(Invalid):rollback_mtp(self.manager,resources())
        (self.mount/'kc-sync/results'/(req['job_id']+'.json')).write_bytes(canonical(receipt(req)));self.connect()
        self.assertEqual(rollback_mtp(self.manager,resources()),3)

    def test_interrupted_withdraw_retries_remaining_entries(self):
        install_mtp(self.manager,resources());delete=self.driver.delete_file_or_folder
        def disconnected(node):
            delete(node);raise OSError('disconnected after first deletion')
        self.driver.delete_file_or_folder=disconnected
        with self.assertRaises(OSError):rollback_mtp(self.manager,resources())
        self.connect();self.assertEqual(rollback_mtp(self.manager,resources()),2)

    def test_usb_never_uses_mtp_even_with_switch_on(self):
        from plugin.install import install,rollback
        for name in ('system','audible'):(self.mount/name).mkdir()
        manager=SimpleNamespace(is_device_present=True,connected_device=SimpleNamespace(_main_prefix=str(self.mount),name='Kindle'))
        self.assertFalse(can_enable(manager));self.assertTrue(can_enable(self.manager))
        with patch('plugin.mtp.enabled',return_value=True):
            store=connected_store(manager)
            self.assertFalse(getattr(store,'experimental_mtp',False))
            backup=install(manager,resources())
        self.assertFalse(list((self.mount/'documents').glob('*-MTP.sh')))
        self.assertEqual(rollback(manager,backup),3)
        with self.assertRaises(Invalid):install_mtp(manager,resources())


class ShellHandoff(unittest.TestCase):
    def setUp(self):
        from executor_tests import Fixture
        self.f=Fixture();self.addCleanup(self.f.close)
        # Native fixture uses Windows absolute paths; these are not MTP-relative metadata.
        (self.f.mount/'metadata.calibre').write_bytes(b'[]')
        (self.f.mount/'kc-sync/state/mtp.json').write_bytes(canonical(dict(schema='kc-mtp/v1',device=self.f.s['device'])))
        self.driver=Driver(self.f.mount)
        self.store=MTPStore(SimpleNamespace(is_device_present=True,connected_device=self.driver))
        self.store.transfer_record=lambda:self.f.root/'transfer.json'
        # This shell harness uses host paths in cc.db so real files can be checked.
        # Discovery requires Kindle /mnt/us paths and is exercised by MTP tests
        # above and IndependentFiles.test_mtp_independent_file_can_be_queued_with_v2.
        # Keep shell handoff checks on the native fixture's exact snapshot.
        from plugin.transport import DeviceStore
        self.store.snapshot=lambda scan_new=True:DeviceStore.snapshot(self.store,scan_new=False)
    def test_v2_handoff_resolves_and_executes_once(self):
        from plugin.protocol import seal
        from plugin.planner import intent
        alias='kc-new-'+'a'*64
        book=self.f.s['books'][2];real=book['uuid'];path=Path(book['location'])
        descriptor=dict(alias=alias,location=book['location'],size=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        book['uuid']=alias
        req=seal(dict(make_request(self.f.s,[intent('add_members','c2',dict(members=[alias]))]),schema='kc-edit-request/v2',new_books=[descriptor]),'request_digest')
        # Native fixture paths replace /mnt/us only in the SQL path validator.
        sql=self.f.device/'validate-request.sql'
        sql.write_text(sql.read_text(encoding='utf8').replace('/mnt/us/documents/',self.f.mount.as_posix()+'/documents/'),encoding='utf8')
        self.store.queue(req,'edit')
        out=self.f.run();self.assertEqual(out.returncode,0,self.f.logs())
        from plugin.protocol import loads
        result=loads((self.f.mount/'kc-sync/results'/(req['job_id']+'.json')).read_bytes())
        self.assertEqual(result['book_bindings'],[dict(alias=alias,uuid=real)])
        self.assertTrue(all(x['status']=='confirmed' for x in result['operations']))
        count=len(self.f.posts());self.assertGreater(count,0)
        self.assertEqual(self.f.run().returncode,0);self.assertEqual(len(self.f.posts()),count)

    def test_real_shell_executes_committed_request_once(self):
        req=make_request(self.f.s);self.store.send(req)
        result=self.f.run();self.assertEqual(result.returncode,0,(result.stdout,result.stderr))
        self.assertTrue(all(x['status']=='confirmed' for x in self.f.result(req)['operations']))
        calls=len(self.f.posts());self.assertGreater(calls,0)
        self.assertEqual(self.f.run().returncode,0);self.assertEqual(len(self.f.posts()),calls)
    def test_uncommitted_and_corrupted_files_never_execute(self):
        req=make_request(self.f.s);self.store.send(req)
        ready=self.f.mount/'kc-sync/mtp-inbox'/(req['job_id']+'.ready');data=ready.read_bytes();ready.unlink()
        self.assertEqual(self.f.run().returncode,0);self.assertEqual(self.f.posts(),[])
        ready.write_bytes(data)
        (self.f.mount/'kc-sync/mtp-inbox'/(req['job_id']+'.json')).write_bytes(b'{}')
        self.assertNotEqual(self.f.run().returncode,0);self.assertEqual(self.f.posts(),[])
    def test_explicit_probe_entry_required_then_six_steps(self):
        req=prepare_probe(self.f.s,'b2','library');self.store.send_probe(req)
        self.assertNotEqual(self.f.run().returncode,0);self.assertEqual(self.f.posts(),[])
        out=self.f.run(['--self-test']);self.assertEqual(out.returncode,0,(out.stdout,out.stderr))
        self.assertTrue(all(x['status']=='confirmed' for x in self.f.result(req)['operations']))


if __name__=='__main__':
    import json,time
    started=time.perf_counter()
    suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(c) for c in (MTP,ShellHandoff))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    (ROOT/'mtp-results.json').write_text(json.dumps(dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),seconds=time.perf_counter()-started,boundary='Simulated MTP using Calibre filesystem objects; actual shell and SQLite 3.26; simulated native service; no KPW6 hardware'),indent=2),encoding='utf-8')
    if not result.wasSuccessful():raise SystemExit(1)
