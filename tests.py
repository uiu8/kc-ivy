"""Isolated D1/D2 contracts, actual SQLite 3.26 and transport failure tests.

No real device, user Calibre database, /change service or ebook is modified.
"""
import sys
import pathlib
if not __debug__ and __name__ == '__main__':
    exec(compile(pathlib.Path(__file__).read_text(encoding='utf-8'), __file__, 'exec', optimize=0), globals())
    raise SystemExit(0)
import copy
import hashlib
import json
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from plugin.protocol import Invalid, KINDS, SCHEMAS, canonical, digest, loads, seal, validate, check, Cancelled, cancellable
from plugin.planner import Catalog, intent, move, plan, request, state_digest
from plugin.legacy import import_collections
from plugin.ledger import adopt, empty_ledger, source_delta, validate_receipt, reconcile_column
from plugin.transport import DeviceStore, atomic_write, connected_store


def snapshot(verified=True, n=3):
    return dict(schema='kc-bookshelf-export/v2', snapshot_id='snapshot-1', generated_utc='2026-10-04T00:00:00Z',
        device=dict(instance_id='device-1', storage_uuid='store-1', serial_sha256=None), firmware='fixture-only',
        capabilities=dict(version='simulator', verified_operations=list(KINDS) if verified else [], evidence='SIMULATOR ONLY'),
        policy=dict(version=1, protected_collections=[], max_members=350, max_request_bytes=262144,
                    max_creates=50, max_rename_delete=10),
        mapping=dict(stable=True, calibre_library_uuid=None, metadata_sha256=''),
        books=[dict(uuid='b'+str(i), title='书籍'+str(i), location='/mnt/us/documents/书'+str(i)+'.azw3',
                    cde_key='key'+str(i), cde_type='EBOK', calibre_uuid='calibre-'+str(i), metadata_matches=1,
                    collection_count=1 if i<2 else 0) for i in range(n)],
        collections=[dict(uuid='c1', name='待读', complete=True),dict(uuid='c2', name='文学', complete=True)],
        relations=[dict(collection_uuid='c1', book_uuid='b'+str(i), member_type='EBOK', member_key='key'+str(i),
                       member_present=1, sideloaded=1, order=i) for i in range(min(n,2))])


def make_request(s=None, intentions=None):
    s = s or snapshot()
    intentions = intentions or [intent('add_members', 'c2', dict(members=['b0']))]
    p = plan(s, intentions, ['c1','c2'], 'library')
    return request(p, s, p.data['inputs_digest'])


def receipt(req, statuses=None):
    statuses = statuses or {}
    return seal(dict(schema='kc-edit-result/v1', job_id=req['job_id'], request_digest=req['request_digest'],
        device=req['device'], operations=[dict(op_id=o['op_id'],status=statuses.get(o['op_id'],'confirmed'),
                                             observed_digest=o['after'], message='simulation') for o in req['operations']],
        snapshot_id=None), 'result_digest')


def mount_fixture(base, s):
    for name in ('state','snapshots','inbox','results'):
        (base/'kc-sync'/name).mkdir(parents=True,exist_ok=True)
    (base/'driveinfo.calibre').write_bytes(canonical(dict(device_store_uuid=s['device']['storage_uuid'])))
    (base/'kc-sync/state/device.json').write_bytes(canonical(s['device']))
    (base/'kc-sync/snapshots/latest.json').write_bytes(canonical(s))
    return DeviceStore(base)


class Contracts(unittest.TestCase):
    def test_streaming_digest_matches_canonical_bytes(self):
        s=snapshot(n=1000)
        self.assertEqual(digest(s),hashlib.sha256(canonical(s)).hexdigest())

    def test_compiled_and_reference_schema_validation_agree(self):
        req=make_request(); result=receipt(req)
        p=plan(snapshot(),[intent('verify_state','c1')],['c1'],'lib').data
        for good in (snapshot(),p,req,result):
            schema=SCHEMAS[good['schema']]
            check(schema,good); validate(good)
            for key in good:
                bad=copy.deepcopy(good); del bad[key]
                with self.assertRaises(Invalid): check(schema,bad)
                with self.assertRaises(Invalid): validate(bad)
        for wrong in (True,1.5,'1',-1):
            bad=snapshot(); bad['policy']['version']=wrong
            with self.assertRaises(Invalid): check(SCHEMAS[bad['schema']],bad)
            with self.assertRaises(Invalid): validate(bad)

    def test_cancel_stops_without_plan_or_writes(self):
        import threading
        event=threading.Event(); event.set()
        with cancellable(event),self.assertRaises(Cancelled):
            plan(snapshot(),[intent('verify_state','c1')],['c1'],'lib')

    def test_duplicate_keys_and_nonfinite_rejected(self):
        for raw in (b'{"a":1,"a":2}',b'{"x":NaN}',b'{"x":Infinity}',b'\xff'):
            with self.assertRaises(Invalid): loads(raw)

    def test_unknown_schema_field_or_command_rejected(self):
        for mutate in (lambda s:s.update(schema='unknown'),lambda s:s.update(sql='DELETE'),
                       lambda s:s['books'][0].update(unknown=1)):
            s=snapshot(); mutate(s)
            with self.assertRaises(Invalid): validate(s)

    def test_duplicate_ids_and_false_complete_rejected(self):
        s=snapshot(); s['books'].append(s['books'][0])
        with self.assertRaises(Invalid): validate(s)
        s=snapshot(); s['relations'][0]['book_uuid']='missing'
        with self.assertRaises(Invalid): validate(s)
        s['collections'][0]['complete']=False
        validate(s)

    def test_membership_union_preserves_unmentioned_manual_books(self):
        s=snapshot(); p=plan(s,[intent('add_members','c1',dict(members=['b2']))],['c1'],'lib')
        self.assertTrue(p.ready)
        self.assertEqual(p.data['operations'][0]['after'],state_digest('待读',{'b0','b1','b2'}))

    def test_explicit_removal_keeps_other_book(self):
        s=snapshot(); p=plan(s,[intent('remove_members','c1',dict(members=['b0']))],['c1'],'lib')
        self.assertEqual(p.data['operations'][0]['after'],state_digest('待读',{'b1'}))

    def test_device_only_book_is_editable_without_calibre_uuid(self):
        s=snapshot(); s['books'][0]['calibre_uuid']=None; s['books'][0]['metadata_matches']=0
        self.assertTrue(plan(s,move(['b0'],'c1','c2'),['c1','c2'],'lib').ready)

    def test_move_depends_on_add_and_rename_keeps_uuid(self):
        s=snapshot(); p=plan(s,move(['b0'],'c1','c2'),['c1','c2'],'lib')
        a,b=p.data['operations']
        self.assertEqual(b['depends_on'],[a['op_id']])
        q=plan(s,[intent('rename_collection','c1',dict(name='阅读'))],['c1'],'lib')
        self.assertEqual(q.data['operations'][0]['collection_uuid'],'c1')
        self.assertEqual(q.data['operations'][0]['after'],state_digest('阅读',{'b0','b1'}))

    def test_empty_create_and_delete_are_explicit(self):
        s=snapshot(); q=plan(s,[intent('create_collection','new',dict(name='空架'))],[],'lib')
        self.assertEqual(q.data['operations'][0]['after'],state_digest('空架',set()))
        p=plan(s,[],['c1','c2'],'lib'); self.assertEqual(p.data['operations'],[])
        q=plan(s,[intent('delete_collection','c1')],['c1'],'lib')
        self.assertIsNone(q.data['operations'][0]['after'])

    def test_scope_protection_unknown_relation_and_capability_block(self):
        for change,scope,code in ((lambda s:None,[],'OUT_OF_SCOPE'),
            (lambda s:s['policy']['protected_collections'].append('c1'),['c1'],'PROTECTED'),
            (lambda s:s['collections'][0].update(complete=False),['c1'],'INCOMPLETE_MEMBERS'),
            (lambda s:s['capabilities'].update(verified_operations=[]),['c1'],'CAPABILITY_UNVERIFIED')):
            s=snapshot(); change(s)
            p=plan(s,[intent('add_members','c1',dict(members=['b2']))],scope,'lib')
            self.assertIn(code,[b['code'] for b in p.data['blockers']])
            with self.assertRaises(Invalid): request(p,s,p.data['inputs_digest'])

    def test_limit_even_when_removing_from_large_shelf(self):
        s=snapshot(); s['policy']['max_members']=0
        p=plan(s,[intent('remove_members','c1',dict(members=['b0']))],['c1'],'lib')
        self.assertIn('MEMBER_LIMIT',[x['code'] for x in p.data['blockers']])

    def test_two_rename_targets_and_delete_edit_conflict(self):
        s=snapshot()
        for intents,code in (([intent('rename_collection','c1',dict(name='A')),intent('rename_collection','c1',dict(name='B'))], 'RENAME_CONFLICT'),
            ([intent('add_members','c1',dict(members=['b2'])),intent('delete_collection','c1')],'DELETE_EDIT_CONFLICT')):
            p=plan(s,intents,['c1'],'lib')
            self.assertIn(code,[x['code'] for x in p.data['blockers']])

    def test_plan_immutable_and_request_exactly_matches_preview(self):
        s=snapshot(); p=plan(s,move(['b0'],'c1','c2'),['c1','c2'],'lib')
        temporary=p.data; temporary['operations'].clear()
        req=request(p,s,p.data['inputs_digest'])
        self.assertEqual(req['operations'],p.data['operations'])
        self.assertEqual(req['plan_digest'],p.data['plan_digest'])
        self.assertEqual(len(req['operations']),2)

    def test_changed_snapshot_inputs_and_tampered_digest_rejected(self):
        s=snapshot(); p=plan(s,[intent('verify_state','c1')],['c1'],'lib')
        altered=copy.deepcopy(s); altered['collections'][0]['name']='other'
        for snap,fp in ((altered,p.data['inputs_digest']),(s,'0'*64)):
            with self.assertRaises(Invalid): request(p,snap,fp)
        r=make_request(); r['operations'][0]['args']['members'].append('b1')
        with self.assertRaises(Invalid): validate(r)

    def test_dangling_and_cycle_dependencies_rejected(self):
        s=snapshot(); i=intent('verify_state','c1',depends_on=['missing'])
        with self.assertRaises(Invalid): plan(s,[i],['c1'],'lib')


class ImportsAndClaims(unittest.TestCase):
    def test_large_source_delta_batches_members_once_per_collection(self):
        ledger=empty_ledger(snapshot()['device'],'lib')
        edges=[('c1','b'+str(i)) for i in range(10000)]
        changed=source_delta(ledger,'column',edges,[b for c,b in edges])
        self.assertEqual(len(changed['intents']),1)
        self.assertEqual(len(changed['proposal']['claims']),10000)

    def test_legacy_asin_and_path_hash_multishelf(self):
        s=snapshot(); path=s['books'][0]['location']
        value={'待读@en-US':{'items':['#key0^EBOK']},'家@书@en-US':{'items':['*'+hashlib.sha1(path.encode('utf-8')).hexdigest()]}}
        out=import_collections(value,s)
        self.assertFalse(out['issues'])
        self.assertEqual([x['args']['members'] for x in out['intents'] if x['kind']=='add_members'],[['b0'],['b0']])
        self.assertEqual(out['intents'][1]['args']['name'],'家@书')

    def test_missing_empty_and_bad_tokens_do_not_clear(self):
        s=snapshot()
        for value in ({},{'待读@en-US':{'items':[]}}):
            self.assertEqual(import_collections(value,s)['intents'],[])
        out=import_collections({'待读@en-US':{'items':['#bad^EBOK']}},s,{'待读@en-US':'c1'},True)
        self.assertTrue(out['issues']); self.assertFalse(out['intents'])

    def test_explicit_empty_replacement_and_duplicate_token_ambiguity(self):
        s=snapshot(); out=import_collections({'待读@en-US':{'items':[]}},s,{'待读@en-US':'c1'},True)
        self.assertEqual(out['intents'][0]['args']['members'],['b0','b1'])
        s['books'][1]['cde_key']='key0'
        out=import_collections({'待读@en-US':{'items':['#key0^EBOK']}},s)
        self.assertTrue(out['issues']); self.assertFalse(out['intents'])

    def test_settings_file_not_treated_as_collections(self):
        with self.assertRaises(Invalid): import_collections({'Rows':[],'Settings':{}},snapshot())

    def test_other_source_claim_prevents_remove_and_missing_record_preserved(self):
        ledger=empty_ledger(snapshot()['device'],'lib')
        ledger=adopt(adopt(ledger,'A',[('c1','b0')]),'B',[('c1','b0')])
        self.assertEqual(source_delta(ledger,'A',[],['b0'])['intents'][0]['kind'],'verify_state')
        self.assertEqual(source_delta(ledger,'A',[],[])['intents'],[])
        ledger=adopt(empty_ledger(snapshot()['device'],'lib'),'A',[('c1','b0')])
        self.assertEqual(source_delta(ledger,'A',[],['b0'])['intents'][0]['kind'],'remove_members')

    def test_tombstone_prevents_source_resurrection(self):
        ledger=empty_ledger(snapshot()['device'],'lib'); ledger['tombstones']=['c1']
        out=source_delta(ledger,'A',[('c1','b0')],['b0'])
        self.assertTrue(out['conflicts']); self.assertFalse(out['intents'])

    def test_receipt_binds_digest_and_cannot_confirm_failed_dependency(self):
        req=make_request(intentions=move(['b0'],'c1','c2'))
        good=receipt(req); validate_receipt(req,good)
        bad=receipt(req,{req['operations'][0]['op_id']:'pending'})
        with self.assertRaises(Invalid): validate_receipt(req,bad)
        bad=copy.deepcopy(good); bad['request_digest']='0'*64; bad=seal(bad,'result_digest')
        with self.assertRaises(Invalid): validate_receipt(req,bad)
        bad=copy.deepcopy(good); bad['operations'].pop(); bad=seal(bad,'result_digest')
        with self.assertRaises(Invalid): validate_receipt(req,bad)

    def test_three_way_merge_preserves_user_edits(self):
        self.assertEqual(reconcile_column(['A','B'],['A','C'],['B','D']),['C','D'])


class USB(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='.usb-',dir=ROOT)
        self.mount=Path(self.temp.name)
        self.s=snapshot(); self.store=mount_fixture(self.mount,self.s)
    def tearDown(self): self.temp.cleanup()

    def test_auto_read_send_repeat_receive_no_path_dialog(self):
        from types import SimpleNamespace
        manager=SimpleNamespace(is_device_present=True,connected_device=SimpleNamespace(_main_prefix=str(self.mount)))
        self.assertEqual(connected_store(manager).snapshot(),self.s)
        req=make_request(self.s); path=self.store.send(req)
        self.assertEqual(loads(path.read_bytes()),req)
        self.assertEqual(self.store.send(req),path)
        atomic_write(self.mount/'kc-sync/results'/path.name,receipt(req))
        self.assertEqual(self.store.receipts()[0]['job_id'],req['job_id'])
        manager.is_device_present=False
        with self.assertRaises(Invalid): connected_store(manager)

    def test_drive_path_change_uses_identity_not_letter(self):
        renamed=self.mount.with_name(self.mount.name+'-moved')
        self.mount.rename(renamed)
        try:
            self.assertEqual(DeviceStore(renamed,self.s['device']).snapshot(),self.s)
        finally: renamed.rename(self.mount)

    def test_wrong_device_and_clone_storage_mismatch_blocked(self):
        wrong=copy.deepcopy(self.s['device']); wrong['instance_id']='other'
        with self.assertRaises(Invalid): DeviceStore(self.mount,wrong)
        (self.mount/'driveinfo.calibre').write_bytes(canonical(dict(device_store_uuid='other')))
        with self.assertRaises(Invalid): DeviceStore(self.mount)

    def test_pending_job_and_partial_publication_blocked(self):
        req=make_request(self.s); self.store.send(req)
        with self.assertRaises(Invalid): self.store.send(make_request(self.s))
        (self.mount/'kc-sync/inbox'/('orphan.json.partial')).write_text('{')
        # Verifying an identical already-published job must not republish it.
        self.assertEqual(loads(self.store.send(req).read_bytes()),req)
        self.assertTrue((self.mount/'kc-sync/inbox/orphan.json.partial').exists())
        with self.assertRaises(Invalid): self.store.send(make_request(self.s))

    def test_same_id_different_content_and_capability_rejected(self):
        req=make_request(self.s); self.store.send(req)
        altered=copy.deepcopy(req); altered['library_uuid']='another'; altered=seal(altered,'request_digest')
        with self.assertRaises(Invalid): self.store.send(altered)
        s=snapshot(False); mount_fixture(self.mount,s)
        req['snapshot_digest']=digest(s); req=seal(req,'request_digest')
        with self.assertRaises(Invalid): self.store.send(req)

    def test_stale_snapshot_and_corrupt_receipt_blocked(self):
        req=make_request(self.s)
        self.s['snapshot_id']='new'; mount_fixture(self.mount,self.s)
        with self.assertRaises(Invalid): self.store.send(req)
        (self.mount/'kc-sync/results/corrupt.json').write_text('{')
        with self.assertRaises(Invalid): self.store.receipts()


class ActualSQLite(unittest.TestCase):
    def test_complete_snapshot_preserves_orphans_and_cc_database(self):
        cli=Path('D:/software/KC-development/sqlite-3.26.0/tools/sqlite-tools-win32-x86-3260000/sqlite3.exe')
        self.assertTrue(cli.exists())
        self.assertTrue(subprocess.check_output([str(cli),'-version']).startswith(b'3.26.0'))
        with tempfile.TemporaryDirectory(prefix='.sql-',dir=ROOT) as temp:
            base=Path(temp); db=base/'cc.db'; metadata=base/'metadata.json'
            con=sqlite3.connect(db)
            con.executescript('''CREATE TABLE Entries(p_uuid TEXT PRIMARY KEY,p_type TEXT,p_titles_0_nominal TEXT,
                p_location TEXT,p_cdeKey TEXT,p_cdeType TEXT,p_collectionCount INTEGER);
                CREATE TABLE Collections(i_collection_uuid TEXT,i_member_uuid TEXT,i_member_cde_type TEXT,
                i_member_cde_key TEXT,i_member_is_present INTEGER,i_is_sideloaded INTEGER,i_order INTEGER);
                INSERT INTO Entries VALUES('b0','Entry:Item','书','/mnt/us/documents/a.azw3','K','EBOK',1);
                INSERT INTO Entries VALUES('c1','Collection','含未知成员',NULL,NULL,NULL,NULL);
                INSERT INTO Entries VALUES('c2','Collection','空架',NULL,NULL,NULL,NULL);
                INSERT INTO Collections VALUES('c1','b0','EBOK','K',1,1,0);
                INSERT INTO Collections VALUES('c1','remote','EBOK','REMOTE',0,0,1);
                INSERT INTO Collections VALUES('gone','remote','EBOK','REMOTE',0,0,0);''')
            con.commit(); con.close()
            before=hashlib.sha256(db.read_bytes()).hexdigest()
            metadata.write_bytes(canonical([dict(lpath='documents/a.azw3',uuid='calibre0')]))
            context=dict(snapshot_id='sql',device=snapshot()['device'],policy=snapshot()['policy'],firmware='fixture',
                         metadata_path=str(metadata).replace('\\','/'),metadata_sha256='hash',mapping_stable=1,library_uuid=None)
            command="CREATE TEMP TABLE run_context(body TEXT); INSERT INTO run_context VALUES('"+canonical(context).decode().replace("'","''")+"');\n"
            command+=(ROOT/'device/export.sql').read_text(encoding='utf-8')
            result=subprocess.run([str(cli),'-batch','-bail','-readonly',str(db)],input=command.encode('utf-8'),capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr.decode())
            exported=validate(loads(result.stdout))
            self.assertEqual(len(exported['relations']),3)
            self.assertFalse(exported['collections'][0]['complete'])
            self.assertTrue(exported['collections'][1]['complete'])
            self.assertEqual(exported['books'][0]['calibre_uuid'],'calibre0')
            self.assertEqual(exported['capabilities']['verified_operations'],[])
            self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(),before)
            (ROOT/'fixtures').mkdir(exist_ok=True)
            (ROOT/'fixtures/sqlite-v2-snapshot.json').write_bytes(canonical(exported))


def main():
    started=time.perf_counter()
    suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls)
                             for cls in (Contracts,ImportsAndClaims,USB,ActualSQLite))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report=dict(tests=result.testsRun, failures=len(result.failures),errors=len(result.errors),
                seconds=time.perf_counter()-started,device_writes=0,
                boundary='Host contracts, SQLite 3.26 readonly fixture and simulated USB only; not native Kindle acceptance')
    (ROOT/'test-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if not result.wasSuccessful() or not result.testsRun: raise SystemExit(1)


if __name__=='__main__': main()
