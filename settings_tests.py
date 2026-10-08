import copy,tempfile,unittest
from unittest.mock import patch
from plugin.service import Service
from plugin.protocol import Invalid
from plugin.workflow import adopt_profile,prepare
from plugin.baseline import align
from workflow_tests import meta
from tests import snapshot
class Settings(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.v=Service(self.tmp.name);self.s=snapshot();self.m=meta()
  p=adopt_profile(self.s,self.m,'library','#shelf',include_current=True);p,_=align(self.s,self.m,p)
  self.p=self.v.state.save(self.v.key('library',self.s),p,0)
 def save(self,selected,field='#shelf',revision=None):
  with patch('plugin.service.read_metadata',return_value=self.m),patch('plugin.column_io.assert_field'),patch.object(Service,'metadata',return_value=self.m):
   return self.v.configure(None,'library',self.s,field,selected,[],self.p['settings'],revision or self.p['revision'],self.m['fingerprint'])
 def test_shrink_preserves_device(self):
  p=self.save({});self.assertEqual(p['ledger']['claims'],{});self.assertFalse(prepare(self.s,self.m,p)['plan'].data['operations'])
 def test_settings_dont_eat_pending_column_edit(self):
  self.m=meta({'calibre-0':[],'calibre-1':['待读'],'calibre-2':[]})
  p=self.save({u:b['copies'] for u,b in self.p['column_baseline'].items()})
  self.assertIn('remove_members',[o['kind'] for o in prepare(self.s,self.m,p)['plan'].data['operations']])
 def test_rebind_preserves_exclusions(self):
  for r in self.m['rows']:r['fields']['#new']=list(r['fields']['#shelf'])
  self.p['ledger']['exclusions']=['keep'];self.p=self.v.state.save(self.v.key('library',self.s),self.p,self.p['revision'])
  p=self.save({'calibre-0':['b0']},'#new');self.assertEqual(p['ledger']['exclusions'],['keep']);self.assertEqual(p['field'],'#new')
 def test_stale_rejected(self):
  with self.assertRaises(Invalid):self.save({},revision=99)
 def test_pending_rejected(self):
  with patch.object(self.v.state,'job_summaries',return_value=[dict(status='staged')]):
   with self.assertRaises(Invalid):self.save({})
if __name__=='__main__':
 r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Settings));raise SystemExit(not r.wasSuccessful())
