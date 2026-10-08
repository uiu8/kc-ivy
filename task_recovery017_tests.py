import tempfile,unittest,copy
from pathlib import Path
from tests import snapshot,mount_fixture,make_request,receipt
from plugin.service import Service
from plugin.protocol import Invalid,canonical
class TaskRecovery(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.s=snapshot();self.store=mount_fixture(self.root,self.s);self.svc=Service(self.root/'local');self.req=make_request(self.s)
  self.job=dict(request=self.req,status='staged',snapshot=self.s,inputs={},manual=[],effects={})
  self.key=self.svc.key('library',self.s);self.svc.state.stage(self.key,self.job)
 def fresh(self):
  s=copy.deepcopy(self.s);s['snapshot_id']='fresh';self.store.path('snapshots/latest.json').write_bytes(canonical(s))
 def test_publish_record_and_idempotent_after_refresh(self):
  self.svc.publish(self.store,self.req);self.assertTrue(self.svc.state.job(self.req['job_id'])['publication_verified']);self.fresh()
  self.svc.publish(self.store,self.req);self.assertEqual(len(list(self.store.path('inbox').glob('*.json'))),1)
 def test_error_record(self):
  self.store.path('snapshots/latest.json.partial').write_bytes(b'')
  with self.assertRaises(Invalid):self.svc.publish(self.store,self.req)
  self.assertEqual(self.svc.state.job(self.req['job_id'])['transfer_events'][-1]['phase'],'error')
 def test_absent_legacy_recovery_requires_new_snapshot(self):
  with self.assertRaises(Invalid):self.svc.recover_draft('library',self.store,self.job)
  self.fresh();s,values=self.svc.recover_draft('library',self.store,self.job)
  self.assertEqual(values[0]['args'],self.req['operations'][0]['args']);self.assertEqual(self.svc.state.job(self.req['job_id'])['status'],'closed')
 def test_existing_execution_blocks_duplicate(self):
  self.fresh();self.store.path('state/jobs/'+self.req['job_id']).mkdir(parents=True)
  with self.assertRaises(Invalid):self.svc.recover_draft('library',self.store,self.job)
 def test_known_sent_missing_not_republished(self):
  self.fresh();self.job['publication_verified']=True
  with self.assertRaises(Invalid):self.svc.recover_draft('library',self.store,self.job)
 def test_uncertain_execution_blocks(self):
  self.fresh();self.store.path('state/pending.json').write_bytes(b'{}')
  with self.assertRaises(Invalid):self.svc.recover_draft('library',self.store,self.job)
 def test_partial_result_restores_only_unconfirmed(self):
  from unittest.mock import patch
  from plugin.planner import intent
  req=make_request(self.s,[intent('add_members','c2',dict(members=['b0'])),intent('remove_members','c1',dict(members=['b1']))])
  job=dict(self.job,request=req,status='closed')
  failed=next(o for o in req['operations'] if o['kind']=='remove_members')
  result=receipt(req,{failed['op_id']:'conflict'})
  self.store.path('results/'+req['job_id']+'.json').write_bytes(canonical(result));self.fresh()
  with patch.object(self.svc.state,'receipt_known',return_value=True):
   _,values=self.svc.recover_draft('library',self.store,job)
  self.assertEqual(len(values),1);self.assertEqual(values[0]['kind'],'remove_members');self.assertEqual(values[0]['args']['members'],['b1'])
if __name__=='__main__':
 r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(TaskRecovery));raise SystemExit(not r.wasSuccessful())
