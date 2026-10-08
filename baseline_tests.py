import copy,unittest,tempfile
from unittest.mock import patch
from plugin.service import Service
from plugin.protocol import Invalid
from workflow_tests import meta
from tests import snapshot
from plugin.workflow import adopt_profile,prepare
from plugin.baseline import align
from plugin.planner import intent
from plugin.ledger import edge_key

class Baseline(unittest.TestCase):
    def setUp(self):
        self.s=snapshot();self.m=meta()
        self.p=adopt_profile(self.s,self.m,'library','#shelf',include_current=True)
    def test_imported_initial_values_allow_manual_remove(self):
        op=intent('remove_members','c1',dict(members=['b0']))
        self.assertTrue(prepare(self.s,self.m,self.p,[op])['issues'])
        p,rows=align(self.s,self.m,self.p)
        self.assertEqual(len(rows),2)
        v=prepare(self.s,self.m,p,[op]);self.assertFalse(v['issues'])
        self.assertEqual([o['kind'] for o in v['plan'].data['operations']],['remove_members'])
        self.assertEqual(align(self.s,self.m,p)[1],[])
        self.assertEqual(self.p['column_baseline']['calibre-0']['values'],[])
    def test_pc_only_add_and_actual_remove_preserved(self):
        p,_=align(self.s,self.m,self.p)
        m=meta({'calibre-0':['文学'],'calibre-1':['待读'],'calibre-2':[]})
        p2,_=align(self.s,m,p)
        kinds={o['kind'] for o in prepare(self.s,m,p2)['plan'].data['operations']}
        self.assertEqual(kinds,{'add_members','remove_members'})
    def test_multimembership(self):
        self.s['relations'].append(dict(self.s['relations'][0],collection_uuid='c2'))
        m=meta({'calibre-0':['待读','文学'],'calibre-1':['待读'],'calibre-2':[]})
        p=adopt_profile(self.s,m,'library','#shelf',include_current=True);p,_=align(self.s,m,p)
        v=prepare(self.s,m,p,[intent('remove_members','c1',dict(members=['b0']))])
        self.assertFalse(v['issues']);self.assertEqual(len(v['plan'].data['operations']),1)
        self.assertEqual(v['plan'].data['operations'][0]['collection_uuid'],'c1')
    def test_exclusions_and_unknown_not_adopted(self):
        self.p['ledger']['exclusions']=[edge_key('c1','b0')]
        p,_=align(self.s,self.m,self.p)
        self.assertNotIn(edge_key('c1','b0'),p['ledger']['claims'])
        self.s['collections'][0]['complete']=False
        self.assertEqual(len(align(self.s,self.m,self.p)[1]),1)
    def test_incomplete_known_edges_calibrate_but_edits_stay_blocked(self):
        self.s['collections'][0]['complete']=False
        self.s['relations'].append(dict(self.s['relations'][0],book_uuid=None))
        before=copy.deepcopy(self.s)
        p,_=align(self.s,self.m,self.p)
        v=prepare(self.s,self.m,p)
        self.assertEqual(v['plan'].data['operations'],[])
        self.assertFalse(v['plan'].data['blockers'])
        edited=prepare(self.s,self.m,p,[intent('remove_members','c1',dict(members=['b0']))])
        self.assertIn('INCOMPLETE_MEMBERS',[x['code'] for x in edited['plan'].data['blockers']])
        self.assertEqual(self.s,before)
    def test_legacy_alias_requires_review_and_preserves_name(self):
        self.s['collections'][0]['name']='A,B'
        m=meta({'calibre-0':['A;B'],'calibre-1':['A;B'],'calibre-2':[]})
        p=adopt_profile(self.s,m,'library','#shelf',include_current=True)
        v=prepare(self.s,m,p)
        self.assertTrue(v['issues']);self.assertFalse(any(o['kind']=='create_collection' for o in v['plan'].data['operations']))
        self.assertEqual(align(self.s,m,p)[1],[])
        aligned,rows=align(self.s,m,p,legacy_names=True)
        self.assertEqual(rows[0]['device_name'],'A,B')
        self.assertEqual(aligned['bindings']['A;B'],'c1')
        self.assertEqual(prepare(self.s,m,aligned)['plan'].data['operations'],[])
        self.assertEqual(self.s['collections'][0]['name'],'A,B')
        from plugin.column_io import import_plan
        imported=import_plan(self.s,m,'#shelf',bindings=aligned['bindings'])
        self.assertEqual(imported[0]['after'],['A;B'])
    def test_ambiguous_legacy_names_not_guessed(self):
        self.s['collections'][0]['name']='A,B;C';self.s['collections'][1]['name']='A;B,C'
        m=meta({'calibre-0':['A;B;C'],'calibre-1':[],'calibre-2':[]})
        p=adopt_profile(self.s,m,'library','#shelf',include_current=True)
        self.assertEqual(align(self.s,m,p,legacy_names=True)[1],[])
    def test_duplicate_known_edge_not_adopted(self):
        self.s['relations'].append(dict(self.s['relations'][0]));self.s['collections'][0]['complete']=False
        p,_=align(self.s,self.m,self.p)
        self.assertEqual(p['column_baseline']['calibre-0']['values'],[])
    def test_partial_copy_agreement_not_adopted(self):
        self.s['books'].append(dict(self.s['books'][0],uuid='copy',location='/mnt/us/documents/copy.azw3'))
        p=adopt_profile(self.s,self.m,'library','#shelf',include_current=True)
        p,_=align(self.s,self.m,p)
        self.assertEqual(p['column_baseline']['calibre-0']['values'],[])
    def test_service_calibration_persists_and_rejects_stale_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            svc=Service(folder);key=svc.key('library',self.s)
            svc.state.save(key,self.p,0)
            with patch.object(svc,'metadata',return_value=self.m):
                preview=svc.baseline_preview(None,'library',self.s)
                self.assertEqual(svc.baseline_apply(None,'library',self.s,preview),2)
                with self.assertRaises(Invalid):svc.baseline_apply(None,'library',self.s,preview)
                self.assertEqual(svc.baseline_preview(None,'library',self.s)['rows'],[])
            self.assertTrue(Service(folder).state.get(key)['ledger']['claims'])

if __name__=='__main__':
    r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Baseline))
    raise SystemExit(0 if r.wasSuccessful() else 1)
