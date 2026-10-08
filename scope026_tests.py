import copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from tests import snapshot,receipt,mount_fixture
from workflow_tests import meta
from plugin.service import Service
from plugin.workflow import adopt_profile,apply_effects
from plugin.protocol import Invalid
from plugin.scope import expand,available_books
from plugin.planner import request

class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.service=Service(Path(self.tmp.name)/'state');self.s=snapshot();self.m=meta()
        self.p=adopt_profile(self.s,self.m,'library','#shelf',{'calibre-0':['b0']})
        self.p['scope_mode']='all';self.key=self.service.key('library',self.s)
        self.p=self.service.state.save(self.key,self.p,0)
    def preview(self):
        with patch.object(self.service,'metadata',return_value=self.m):return self.service.preview(None,'library',self.s,[])
    def test_new_books_empty_column_preserves_device_members(self):
        self.s['relations'].append(dict(self.s['relations'][0],book_uuid='b2',member_key='key2'))
        value=self.preview();ops=value['plan'].data['operations']
        self.assertFalse(value['issues']);self.assertFalse(any(o['kind']=='remove_members' for o in ops))
        self.assertEqual(set(self.service.profile('library',self.s)['column_baseline']),{'calibre-0','calibre-1','calibre-2'})
        revision=self.service.state.revision(self.key);self.preview();self.assertEqual(revision,self.service.state.revision(self.key))
    def test_future_books_multiple_memberships_are_automatic(self):
        self.preview()
        self.s['books'].append(dict(self.s['books'][0],uuid='new',calibre_uuid='calibre-new',location='/mnt/us/documents/new.azw3'))
        self.m=meta({'calibre-0':['待读'],'calibre-1':['待读'],'calibre-2':[],'calibre-new':['待读','文学']})
        value=self.preview()
        destinations={o['collection_uuid'] for o in value['plan'].data['operations'] if o['kind']=='add_members' and 'new' in o['args']['members']}
        self.assertEqual(destinations,{'c1','c2'})
    def test_existing_book_new_copy_and_pending_edits(self):
        self.s['books'].append(dict(self.s['books'][0],uuid='copy',location='/mnt/us/documents/copy.azw3'))
        self.m=meta({'calibre-0':['待读','文学'],'calibre-1':['待读'],'calibre-2':[]})
        value=self.preview();ops=value['plan'].data['operations']
        self.assertTrue(any(o['kind']=='add_members' and o['collection_uuid']=='c1' and o['args']['members']==['copy'] for o in ops))
        self.assertTrue(any(o['kind']=='add_members' and o['collection_uuid']=='c2' and set(o['args']['members'])=={'b0','copy'} for o in ops))
        p=self.service.profile('library',self.s)
        self.assertEqual(p['column_baseline']['calibre-0']['values'],['待读'])
        req=request(value['plan'],self.s,value['plan'].data['inputs_digest'])
        job=dict(value,request=req)
        first=next(o for o in req['operations'] if o['kind']=='add_members' and o['collection_uuid']=='c1')
        untouched=apply_effects(p,job,[])
        self.assertIn('copy',untouched['column_baseline']['calibre-0']['pending_copies'])
        accepted=apply_effects(p,job,[first])
        self.assertNotIn('copy',accepted['column_baseline']['calibre-0']['pending_copies'])
        self.assertEqual(p['column_baseline']['calibre-0']['pending_copies'],{'copy':['待读']})
    def test_existing_removal_not_recalibrated(self):
        self.m=meta({'calibre-0':[],'calibre-1':['待读'],'calibre-2':[]})
        ops=self.preview()['plan'].data['operations']
        self.assertTrue(any(o['kind']=='remove_members' and o['args']['members']==['b0'] for o in ops))
    def test_selected_scope_preserved(self):
        p=self.service.profile('library',self.s);p.pop('scope_mode')
        self.service.state.save(self.key,p,p['revision']);self.preview()
        self.assertEqual(set(self.service.profile('library',self.s)['column_baseline']),{'calibre-0'})
    def test_bad_mapping_and_scripts_are_not_adopted(self):
        self.s['books'][1]['location']='/mnt/us/documents/entry.SH'
        self.s['books'][2]['metadata_matches']=2
        self.assertEqual(available_books(self.s,self.m),{'calibre-0':['b0']})
        self.s['mapping']['stable']=False
        with self.assertRaises(Invalid):self.preview()
    def test_pending_task_prevents_scope_mutation(self):
        self.m=meta({'calibre-0':['待读','文学'],'calibre-1':['待读'],'calibre-2':[]})
        value=self.preview();mount=Path(self.tmp.name)/'mount';mount.mkdir();store=mount_fixture(mount,self.s)
        with patch.object(self.service,'metadata',return_value=self.m):self.service.submit(None,'library',store,value,[])
        revision=self.service.state.revision(self.key)
        self.s['books'].append(dict(self.s['books'][0],uuid='copy'))
        with self.assertRaises(Invalid):self.preview()
        self.assertEqual(revision,self.service.state.revision(self.key))

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ScopeTests))
    raise SystemExit(not result.wasSuccessful())
