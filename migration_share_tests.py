"""Real, isolated Calibre libraries plus simulated target devices; no user writes."""
import pathlib
if not __debug__ and __name__=='__main__':
    exec(compile(pathlib.Path(__file__).read_text(encoding='utf8'),__file__,'exec',optimize=0),globals());raise SystemExit(0)
import copy,io,tempfile,unittest
from unittest.mock import patch
from zipfile import ZipFile,ZIP_STORED
from calibre.ebooks.metadata.book.base import Metadata
from plugin.sharing import new_library,export_share,inspect_share,import_share,local_backup
from plugin.migration import export_snapshot,new_session,bind_defaults,overview,MigrationService,seal_backup,progress
from plugin.service import Service
from plugin.protocol import Invalid,canonical,digest
from plugin.metadata import mappings,read_metadata
from plugin.retention import eligible
from tests import snapshot,receipt,mount_fixture

class SharingMigration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name)
        self.db=new_library(self.root/'source');self.other=new_library(self.root/'receiver')
        self.api=self.db.new_api;self.target=self.other.new_api;self.lib=str(self.db.library_id);self.receiver=str(self.other.library_id)
        self.ids=[self.api.create_book_entry(Metadata('书'+str(i),['作者'])) for i in range(3)]
        for i in self.ids:self.api.add_format(i,'TXT',io.BytesIO(('正文'+str(i)).encode()),run_hooks=False)
        self.api.set_field('#kindlecollections',{self.ids[0]:['文学','待读'],self.ids[1]:['文学'],self.ids[2]:[]})
        self.svc=Service(self.root/'private');self.path=self.root/'books.kcshare.zip'
        export_share(self.api,self.lib,self.ids,'#kindlecollections',self.path,['TXT'])
        self.manifest=inspect_share(self.path)
    def tearDown(self):self.db.close();self.other.close();self.tmp.cleanup()
    def target_snapshot(self,api=None,library=None):
        api=api or self.api;library=library or self.lib;s=snapshot();s['mapping']['calibre_library_uuid']=library
        ids=sorted(api.all_book_ids())
        for b,i in zip(s['books'],ids):b['calibre_uuid']=api.field_for('uuid',i)
        return s
    def test_real_share_multiple_memberships_repeat_and_local_mapping(self):
        j,changed=import_share(self.target,self.receiver,self.path,'#kindlecollections',self.svc.state)
        self.assertEqual(len(changed),3);self.assertNotEqual(j['items'][self.manifest['backup']['books'][0]['id']]['uuid'],self.api.field_for('uuid',self.ids[0]))
        bytitle=self.target.all_field_for('title',self.target.all_book_ids());i=next(i for i,t in bytitle.items() if t=='书0')
        self.assertEqual(set(self.target.field_for('#kindlecollections',i)),{'文学','待读'})
        self.assertEqual(self.target.format(i,'TXT'),self.api.format(self.ids[0],'TXT'))
        again,changed=import_share(self.target,self.receiver,self.path,'#kindlecollections',self.svc.state)
        self.assertEqual(changed,[]);self.assertEqual(len(self.target.all_book_ids()),3)
        backup=local_backup(again);self.assertEqual(backup['source']['library_uuid'],self.receiver)
        session=bind_defaults(new_session(self.svc.state,self.receiver,self.target_snapshot(self.target,self.receiver),backup),self.target_snapshot(self.target,self.receiver))
        self.assertTrue(all(b['targets'] for b in overview(session,self.target_snapshot(self.target,self.receiver))[1]))
    def test_explicit_reuse_preserves_file_and_values(self):
        i=self.target.create_book_entry(Metadata('自己的版本',['自己']));self.target.add_format(i,'TXT',io.BytesIO(b'original'),run_hooks=False)
        self.target.set_field('#kindlecollections',{i:['自己的分类']})
        bid=self.manifest['backup']['books'][0]['id']
        j,_=import_share(self.target,self.receiver,self.path,'#kindlecollections',self.svc.state,{bid:i})
        self.assertEqual(self.target.format(i,'TXT'),b'original');self.assertEqual(self.target.field_for('title',i),'自己的版本')
        self.assertEqual(set(self.target.field_for('#kindlecollections',i)),{'自己的分类','文学','待读'})
    def test_crash_after_format_write_resumes_without_duplicate(self):
        original=self.target.add_format
        def interrupted(*args,**kwargs):original(*args,**kwargs);raise RuntimeError('simulated disconnect')
        with patch.object(self.target,'add_format',side_effect=interrupted):
            with self.assertRaises(RuntimeError):import_share(self.target,self.receiver,self.path,'#kindlecollections',self.svc.state)
        self.assertEqual(len(self.target.all_book_ids()),1)
        import_share(self.target,self.receiver,self.path,'#kindlecollections',self.svc.state)
        self.assertEqual(len(self.target.all_book_ids()),3)
    def test_changed_interrupted_format_is_not_overwritten(self):
        original=self.target.add_format
        def interrupted(*args,**kwargs):original(*args,**kwargs);raise RuntimeError('stop')
        with patch.object(self.target,'add_format',side_effect=interrupted):
            with self.assertRaises(RuntimeError):import_share(self.target,self.receiver,self.path,'#kindlecollections',self.svc.state)
        i=next(iter(self.target.all_book_ids()));original(i,'TXT',io.BytesIO(b'edited after crash'),run_hooks=False)
        with self.assertRaisesRegex(Invalid,'格式被修改'):import_share(self.target,self.receiver,self.path,'#kindlecollections',self.svc.state)
        self.assertEqual(self.target.format(i,'TXT'),b'edited after crash')
    def test_corrupt_payload_and_path_rejected_before_writes(self):
        bad=self.root/'bad.zip'
        with ZipFile(self.path) as z,ZipFile(bad,'w',ZIP_STORED) as out:
            for name in z.namelist():
                data=z.read(name)
                if name!='manifest.json':data=b'x'*len(data)
                out.writestr(name,data)
        with self.assertRaises(Invalid):import_share(self.target,self.receiver,bad,'#kindlecollections',self.svc.state)
        self.assertFalse(self.target.all_book_ids())
        m=copy.deepcopy(self.manifest);m['files'][0]['path']='books/../evil.txt';m['digest']=digest({k:v for k,v in m.items() if k!='digest'})
        with ZipFile(self.root/'path.zip','w') as out:out.writestr('manifest.json',canonical(m))
        with self.assertRaises(Invalid):inspect_share(self.root/'path.zip')
    def prepare_migration(self):
        s=self.target_snapshot();old=copy.deepcopy(s);old['collections'].append(dict(uuid='old-extra',name='新架',complete=True))
        old['relations'].append(dict(old['relations'][0],collection_uuid='old-extra'))
        backup=export_snapshot(old)
        self.svc.adopt(self.api,self.lib,s,'#kindlecollections',mappings(s,read_metadata(self.api,['#kindlecollections'])['rows']))
        session=new_session(self.svc.state,self.lib,s,backup);ms=MigrationService(self.svc)
        return s,session,ms,ms.prepare(self.api,self.lib,s,session['id'])
    def test_migration_receipt_atomic_progress_column_backfill_and_repeat(self):
        s,session,ms,p=self.prepare_migration();self.assertFalse(p['issues']);self.assertTrue(p['plan'].ready,p['plan'].data['blockers'])
        ops=p['plan'].data['operations'];self.assertEqual([o['kind'] for o in ops],['create_collection','add_members'])
        mount=self.root/'mount';mount.mkdir();store=mount_fixture(mount,s)
        jid=ms.submit(self.api,self.lib,store,p);job=self.svc.state.job(jid)
        self.assertEqual(self.svc.state.workspace(session['id'])['active_job'],jid)
        cid=ops[0]['collection_uuid'];after=copy.deepcopy(s);after['collections'].append(dict(uuid=cid,name='新架',complete=True));after['relations'].append(dict(after['relations'][0],collection_uuid=cid));after['snapshot_id']='after'
        self.svc.receive(self.lib,after,[receipt(job['request'])]);saved=self.svc.state.workspace(session['id'])
        self.assertFalse(saved['active_job']);self.assertTrue(any('b0' in v for v in saved['done'].values()))
        self.assertFalse(eligible(self.svc.state,self.svc.state.job(jid)))
        self.svc.backfill_confirmed(self.api,self.lib,after)
        self.assertIn('新架',self.api.field_for('#kindlecollections',self.ids[0]))
        self.assertTrue(eligible(self.svc.state,self.svc.state.job(jid)))
        again=ms.prepare(self.api,self.lib,after,session['id']);self.assertEqual(again['plan'].data['operations'],[])
        self.assertIn('已完成',progress(self.svc.state.workspace(session['id']),after))
        after['relations']=[r for r in after['relations'] if r['collection_uuid']!=cid]
        again=ms.prepare(self.api,self.lib,after,session['id']);self.assertEqual(again['plan'].data['operations'],[])
    def test_partial_result_requires_recovery_and_preserves_confirmed(self):
        s,session,ms,p=self.prepare_migration();mount=self.root/'mount';mount.mkdir();store=mount_fixture(mount,s)
        jid=ms.submit(self.api,self.lib,store,p);job=self.svc.state.job(jid);ops=job['request']['operations']
        result=receipt(job['request'],{ops[-1]['op_id']:'not_run'})
        self.svc.receive(self.lib,s,[result]);saved=self.svc.state.workspace(session['id'])
        self.assertEqual(saved['active_job'],jid);self.assertIn(ops[0]['collection_uuid'],saved['created_targets'])
        with self.assertRaises(Invalid):ms.prepare(self.api,self.lib,s,session['id'])
        after=copy.deepcopy(s);after['snapshot_id']='refreshed-after-partial'
        after['collections'].append(dict(uuid=ops[0]['collection_uuid'],name='新架',complete=True))
        store.path('snapshots/latest.json').write_bytes(canonical(after));store.path('results/'+jid+'.json').write_bytes(canonical(result))
        snap,resumed=ms.recover(self.lib,store,self.svc.state.job(jid));self.assertFalse(resumed['active_job'])
        self.svc.backfill_confirmed(self.api,self.lib,snap)
        remaining=ms.prepare(self.api,self.lib,snap,session['id']);self.assertEqual([o['kind'] for o in remaining['plan'].data['operations']],['add_members'])
        nextid=ms.submit(self.api,self.lib,store,remaining);self.assertNotEqual(nextid,jid)
    def test_create_limit_missing_and_foreign_identity(self):
        s=self.target_snapshot();p=self.svc.profile(self.lib,s);self.svc.configure_manual(self.lib,s,p['revision'],p['settings'])
        backup=export_snapshot(s);backup['collections']=[dict(id='s'+str(i),name='空架'+str(i)) for i in range(51)];backup['books']=[];backup['relations']=[];backup=seal_backup(backup)
        session=new_session(self.svc.state,self.lib,s,backup);ms=MigrationService(self.svc);prepared=ms.prepare(self.api,self.lib,s,session['id'])
        self.assertEqual(len(prepared['plan'].data['operations']),50);self.assertTrue(prepared['migration_notes'])
        wrong=copy.deepcopy(s);wrong['device']['instance_id']='other'
        with self.assertRaises(Invalid):ms.prepare(self.api,self.lib,wrong,session['id'])
        with self.assertRaises(Invalid):new_library(self.root/'receiver')
    def test_unknowns_missing_copies_and_scripts_never_automatch(self):
        s=self.target_snapshot();s['books'][1]['location']='/mnt/us/documents/entry.sh'
        s['relations'].append(dict(s['relations'][0],book_uuid='missing',member_present=0))
        s['collections'][0]['complete']=False
        backup=export_snapshot(s);self.assertEqual(len(backup['books']),1);self.assertEqual(len(backup['unresolved_relations']),2)
        target=self.target_snapshot();target['books'][1]['calibre_uuid']=target['books'][0]['calibre_uuid']
        session=bind_defaults(new_session(self.svc.state,self.lib,target,backup),target)
        rows,books=overview(session,target);self.assertEqual(books[0]['targets'],[]);self.assertIn('多个副本',books[0]['status'])
        session['matches'][books[0]['id']]=['b0','b1'];rows,books=overview(session,target)
        self.assertEqual(books[0]['targets'],['b0','b1']);self.assertTrue(any(r['waiting'] for r in rows))
    def test_full_final_member_cap_cannot_be_split_away(self):
        s=self.target_snapshot();p=self.svc.profile(self.lib,s);self.svc.configure_manual(self.lib,s,p['revision'],p['settings'])
        s['policy']['max_members']=1
        old=copy.deepcopy(s);old['collections'][0]['name']='超过上限'
        session=new_session(self.svc.state,self.lib,s,export_snapshot(old));value=MigrationService(self.svc).prepare(self.api,self.lib,s,session['id'])
        self.assertFalse(value['plan'].data['operations']);self.assertTrue(any('超过保护上限' in n for n in value['migration_notes']))
    def test_new_device_uuids_never_reuse_old_device_identity(self):
        old=self.target_snapshot();backup=export_snapshot(old);target=copy.deepcopy(old)
        target['device']=dict(instance_id='new-device',storage_uuid='new-storage',serial_sha256=None)
        for book in target['books']:book['uuid']='new-'+book['uuid']
        target['collections']=[];target['relations']=[]
        p=self.svc.profile(self.lib,target);self.svc.configure_manual(self.lib,target,p['revision'],p['settings'])
        session=new_session(self.svc.state,self.lib,target,backup)
        plan=MigrationService(self.svc).prepare(self.api,self.lib,target,session['id'])['plan'].data
        self.assertEqual(plan['device'],target['device']);self.assertFalse(plan['blockers'])
        self.assertEqual({b for o in plan['operations'] for b in o['args'].get('members',[])},{'new-b0','new-b1'})
        self.assertTrue(all(o['collection_uuid'] not in ('c1','c2') for o in plan['operations']))
    def test_selected_book_backup_keeps_multiple_relations_without_other_books(self):
        s=self.target_snapshot();s['relations'].append(dict(s['relations'][0],collection_uuid='c2'))
        s['collections'].append(dict(uuid='empty',name='空架',complete=True))
        value=export_snapshot(s,selected_books={'b0'})
        self.assertEqual(len(value['books']),1);self.assertEqual(len(value['relations']),2)
        self.assertEqual({c['name'] for c in value['collections']},{'文学','待读'})
        self.assertEqual(len(export_snapshot(s,['c1'],{'b0'})['relations']),1)
        self.assertEqual(export_snapshot(s,selected_books=set())['collections'],[])
        full=export_snapshot(s);self.assertEqual(len(full['books']),2);self.assertEqual(len(full['collections']),3)
    def test_collection_local_selection_is_not_a_global_book_filter(self):
        s=self.target_snapshot();s['relations'].append(dict(s['relations'][0],collection_uuid='c2'))
        value=export_snapshot(s,{'c1','c2'},selected_relations={'c1':{'b1'},'c2':{'b0'}})
        names={c['id']:c['name'] for c in value['collections']};books={b['id']:b['title'] for b in value['books']}
        self.assertEqual({(names[c],books[b]) for c,b in value['relations']},{('待读','书籍1'),('文学','书籍0')})
        path=self.root/'partial-share.zip'
        export_share(self.api,self.lib,self.ids,'#kindlecollections',path,['TXT'],selected_names={self.ids[0]:['文学'],self.ids[1]:['文学'],self.ids[2]:[]})
        backup=inspect_share(path)['backup'];self.assertEqual(len(backup['books']),3)
        self.assertEqual({c['name'] for c in backup['collections']},{'文学'})

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SharingMigration))
    if not result.wasSuccessful():raise SystemExit(1)
