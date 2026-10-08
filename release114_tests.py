"""Regression coverage using real temporary Calibre libraries and mounted fixtures."""
import copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from calibre.ebooks.metadata.book.base import Metadata
import migration_share_tests
from plugin.protocol import Invalid,canonical,seal
from plugin.probe import prepare_probe,cancellable_probe,cancel_probe
from plugin.migration import export_snapshot,new_session,MigrationService,progress
from plugin.sharing import export_share,import_share
from tests import snapshot,mount_fixture,receipt,make_request


class LibraryFlows(unittest.TestCase):
    setUp=migration_share_tests.SharingMigration.setUp
    tearDown=migration_share_tests.SharingMigration.tearDown
    target_snapshot=migration_share_tests.SharingMigration.target_snapshot

    def test_share_case_conflict_never_renames_unrelated_books(self):
        for field in ('tags','#kindlecollections'):
            with self.subTest(field=field):
                self.api.set_field(field,{self.ids[0]:['Fantasy']})
                other=self.target.create_book_entry(Metadata('接收者自己的书'))
                self.target.set_field(field,{other:['fantasy','private']})
                before=self.target.all_field_for(field,self.target.all_book_ids());ids=self.target.all_book_ids()
                path=self.root/(field.lstrip('#')+'.zip')
                export_share(self.api,self.lib,[self.ids[0]],field,path,['TXT'])
                with self.assertRaisesRegex(Invalid,'大小写冲突'):
                    import_share(self.target,self.receiver,path,field,self.svc.state)
                self.assertEqual(self.target.all_book_ids(),ids)
                self.assertEqual(self.target.all_field_for(field,ids),before)
                # Matching capitalization can import and resume normally.
                self.api.set_field(field,{self.ids[0]:['fantasy']})
                fixed=path.with_name('fixed-'+path.name)
                export_share(self.api,self.lib,[self.ids[0]],field,fixed,['TXT'])
                journal,changed=import_share(self.target,self.receiver,fixed,field,self.svc.state)
                self.assertTrue(journal['complete']);self.assertEqual(len(changed),1)
                self.assertEqual(set(self.target.field_for(field,other)),{'fantasy','private'})
                self.assertEqual(import_share(self.target,self.receiver,fixed,field,self.svc.state)[1],[])

    def prepare_new_migration(self):
        old=self.target_snapshot();backup=export_snapshot(old)
        s=copy.deepcopy(old);s['books']=s['books'][1:]
        s['relations']=[r for r in s['relations'] if r['book_uuid']!='b0']
        mount=self.root/'mount';mount.mkdir();store=mount_fixture(mount,s)
        (mount/'documents').mkdir();(mount/'documents/new.azw3').write_bytes(b'new book')
        (mount/'documents/KC执行收藏夹任务.sh').write_text('exec sh /mnt/us/kc-sync/runtime/0.6.14/run.sh')
        uid=self.api.field_for('uuid',self.ids[0])
        (mount/'metadata.calibre').write_bytes(canonical([dict(uuid=uid,lpath='documents/new.azw3',title='新书')]))
        current=store.snapshot();alias=current['books'][-1]['uuid']
        self.assertTrue(alias.startswith('kc-new-'))
        p=self.svc.profile(self.lib,current);self.svc.configure_manual(self.lib,current,p['revision'],p['settings'])
        session=new_session(self.svc.state,self.lib,current,backup)
        source=next(b['id'] for b in backup['books'] if b['calibre_uuid']==uid)
        session['matches'][source]=[alias];session=self.svc.state.save_workspace(session,session['revision'])
        migration=MigrationService(self.svc);prepared=migration.prepare(self.api,self.lib,current,session['id'])
        jid=migration.submit(self.api,self.lib,store,prepared);job=self.svc.state.job(jid);req=job['request']
        self.assertEqual(req['schema'],'kc-edit-request/v2');self.assertEqual(req['new_books'][0]['alias'],alias)
        return current,alias,store,migration,session,job,source,old

    def test_migration_new_book_publish_receipt_and_no_repeat(self):
        current,alias,store,migration,session,job,source,old=self.prepare_new_migration()
        req=job['request']
        after=copy.deepcopy(current);after['books'][-1]['uuid']='real-new-book';after['books'][-1]['collection_count']=1
        after['relations'].append(dict(old['relations'][0],book_uuid='real-new-book'));after['snapshot_id']='after-migration'
        result=seal(dict(receipt(req),schema='kc-edit-result/v2',book_bindings=[dict(alias=alias,uuid='real-new-book')]),'result_digest')
        self.svc.receive(self.lib,after,[result]);saved=self.svc.state.workspace(session['id'])
        self.assertEqual(saved['matches'][source],['real-new-book'])
        self.assertTrue(any('real-new-book' in ids for ids in saved['done'].values()))
        self.assertFalse(any(alias in ids for ids in saved['done'].values()))
        self.assertFalse(saved['active_job']);self.assertIn('已完成',progress(saved,after))
        self.assertFalse(migration.prepare(self.api,self.lib,after,session['id'])['plan'].data['operations'])

    def test_migration_recovery_rebinds_unresolved_manual_pair(self):
        current,alias,store,migration,session,job,source,old=self.prepare_new_migration()
        req=job['request']
        result=seal(dict(receipt(req,{o['op_id']:'conflict' for o in req['operations']}),schema='kc-edit-result/v2',book_bindings=[]),'result_digest')
        self.svc.receive(self.lib,current,[result])
        after=copy.deepcopy(current);after['books'][-1]['uuid']='now-indexed';after['snapshot_id']='after-indexing'
        store.path('snapshots/latest.json').write_bytes(canonical(after))
        store.path('results/'+req['job_id']+'.json').write_bytes(canonical(result))
        snap,resumed=migration.recover(self.lib,store,self.svc.state.job(req['job_id']))
        self.assertEqual(resumed['matches'][source],['now-indexed'])
        prepared=migration.prepare(self.api,self.lib,snap,session['id'])
        self.assertEqual([o['args']['members'] for o in prepared['plan'].data['operations'] if o['kind']=='add_members'],[['now-indexed']])


class ProbeFlows(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=mount_fixture(Path(self.tmp.name),snapshot())
        self.task=prepare_probe(snapshot(),'b2','library')
        self.marker=self.store.path('state/probe.json');self.marker.write_bytes(canonical(dict(job_id=self.task['job_id'])))
        self.inbox=self.store.path('inbox/'+self.task['job_id']+'.json');self.inbox.write_bytes(canonical(self.task))

    def test_pending_book_rejected_before_probe_creation(self):
        s=snapshot();alias='kc-new-'+'f'*64;s['books'][2]['uuid']=alias
        with self.assertRaisesRegex(Invalid,'已识别'):prepare_probe(s,alias,'library')
        from plugin.planner import intent
        req=make_request(s,[intent('add_members','c2',dict(members=[alias]))])
        with self.assertRaises(Invalid):self.store.send(req)
        self.assertFalse(self.store.path('inbox/'+req['job_id']+'.json').exists())

    def test_cancel_archives_and_allows_new_probe(self):
        selected=cancellable_probe(self.store);cancel_probe(self.store,selected)
        self.assertFalse(self.marker.exists());self.assertFalse(self.inbox.exists())
        archive=self.store.path('state/probe-cancelled/'+self.task['job_id'])
        self.assertEqual((archive/'request.json').read_bytes(),canonical(self.task))
        self.assertTrue((archive/'marker.json').exists())

    def test_execution_evidence_blocks_cancel(self):
        for path in ('state/jobs/'+self.task['job_id'],'results/'+self.task['job_id']+'.json','state/pending.json'):
            with self.subTest(path=path):
                target=self.store.path(path);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'evidence')
                with self.assertRaisesRegex(Invalid,'执行记录'):cancel_probe(self.store,self.task)
                self.assertTrue(self.inbox.exists());self.assertTrue(self.marker.exists());target.unlink()

    def test_cancel_interrupted_after_request_archive_can_resume(self):
        rename=Path.rename
        def interrupted(path,target):
            result=rename(path,target)
            if path==self.inbox:raise OSError('disconnect after rename')
            return result
        with patch.object(Path,'rename',interrupted),self.assertRaises(OSError):cancel_probe(self.store,self.task)
        self.assertTrue(self.marker.exists());self.assertFalse(self.inbox.exists())
        cancel_probe(self.store,self.task)
        self.assertFalse(self.marker.exists())
