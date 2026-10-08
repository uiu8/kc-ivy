import unittest,tempfile
from pathlib import Path
from unittest.mock import patch
from tests import snapshot,mount_fixture,make_request,receipt
from plugin.state import StateStore
from plugin.protocol import canonical,Invalid
from plugin.retention import prune_completed
from plugin.storage import cleanup_plan,cleanup
class Retention(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.s=snapshot();self.store=mount_fixture(self.root,self.s);self.state=StateStore(self.root/'local');self.jobs=[]
  for i in range(8):
   req=make_request(self.s);res=receipt(req);job=dict(request=req,result=res,status='complete',column_field='')
   self.state.stage('key',job);self.jobs.append(job);jid=req['job_id']
   self.store.path('inbox/'+jid+'.json').write_bytes(canonical(req));self.store.path('results/'+jid+'.json').write_bytes(canonical(res))
   directory=self.store.path('state/jobs/'+jid);directory.mkdir(parents=True);(directory/'request.json').write_bytes(canonical(req));(directory/'progress.json').write_text('[]');(directory/'op-0.confirmed.json').write_text('{}')
 def test_keep_five_both_sides(self):
  self.assertEqual(prune_completed(self.store,self.state,'key'),3)
  self.assertEqual(len(self.state.jobs('key')),5);self.assertEqual(len(list(self.store.path('inbox').glob('*.json'))),5)
  self.assertEqual(len(list(self.store.path('results').glob('*.json'))),5);self.assertEqual(len(list(self.store.path('state/jobs').iterdir())),5)
  self.assertEqual(prune_completed(self.store,self.state,'key'),0)
 def test_unfinished_and_column_pending_preserved(self):
  first=self.jobs[0];self.state.stage_column(dict(job_id=first['request']['job_id'],result_digest=first['result']['result_digest']))
  unfinished=dict(request=make_request(),status='staged');self.state.stage('key',unfinished)
  self.assertEqual(prune_completed(self.store,self.state,'key'),2)
  self.assertIsNotNone(self.state.job(first['request']['job_id']));self.assertIsNotNone(self.state.job(unfinished['request']['job_id']))
 def test_interruption_before_local_delete_resumes(self):
  with patch.object(self.state,'drop_completed',side_effect=RuntimeError('interrupted')):
   with self.assertRaises(RuntimeError):prune_completed(self.store,self.state,'key')
  self.assertEqual(len(self.state.jobs('key')),8);self.assertEqual(prune_completed(self.store,self.state,'key'),3)
 def test_unknown_job_files_preserved(self):
  jid=self.jobs[0]['request']['job_id'];self.store.path('state/jobs/'+jid+'/unknown.txt').write_text('preserve')
  self.assertEqual(prune_completed(self.store,self.state,'key'),2);self.assertIsNotNone(self.state.job(jid))
 def test_changed_receipt_stops(self):
  jid=self.jobs[2]['request']['job_id'];self.store.path('results/'+jid+'.json').write_text('{}')
  with self.assertRaises(Invalid):prune_completed(self.store,self.state,'key')
  self.assertTrue(self.store.path('inbox/'+jid+'.json').exists())
 def test_pending_blocks_and_logs_keep_five(self):
  self.store.path('state/pending.json').write_text('{}');self.assertEqual(prune_completed(self.store,self.state,'key'),0)
  self.store.path('state/pending.json').unlink()
  for folder,pattern in [('logs','KC日志-{}.txt'),('recovery','snapshot-{}.json.interrupted')]:
   self.store.path(folder).mkdir()
   for i in range(9):self.store.path(folder+'/'+pattern.format(i)).write_text('test')
  items=cleanup_plan(self.store,self.state.jobs('key'));self.assertEqual(len(items),8);cleanup(self.store,self.state.jobs('key'),items)
  self.assertEqual(len(list(self.store.path('logs').iterdir())),5);self.assertEqual(len(list(self.store.path('recovery').iterdir())),5)
if __name__=='__main__':
 r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Retention));raise SystemExit(not r.wasSuccessful())
