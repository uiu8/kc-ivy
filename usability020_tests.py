import unittest,tempfile,copy,time,os
from pathlib import Path
from types import SimpleNamespace
from tests import snapshot,make_request,mount_fixture,receipt
from plugin.state import StateStore
from plugin.usability import recovery_items,choose_recovery,plan_rows,task_status
from plugin.planner import intent,move,Catalog,plan
from plugin.protocol import Invalid,canonical
from plugin.storage import cleanup_plan,cleanup,inventory

class Usability(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.state=StateStore(self.root/'local');self.s=snapshot()
 def test_draft_persistent_and_stage_clears(self):
  value=dict(intents=[intent('add_members','c2',dict(members=['b0']))])
  self.state.save_draft('one',value);self.assertEqual(StateStore(self.root/'local').draft('one'),value);self.assertIsNone(self.state.draft('two'))
  self.state.stage('one',dict(request=make_request(),status='staged'));self.assertIsNone(self.state.draft('one'))
 def test_recovery_explicit_missing_and_dependencies(self):
  values=move(['b0'],'c1','c2');values.append(intent('add_members','c2',dict(members=['b1','gone'])))
  old=copy.deepcopy(self.s);old['books'].append(dict(old['books'][0],uuid='gone',title='失效书'))
  items=recovery_items(values,self.s,old);ids={v['intent_id'] for v in values}
  self.assertEqual(items[-1]['missing'][0]['title'],'失效书')
  with self.assertRaises(Invalid):choose_recovery(items,ids)
  chosen=choose_recovery(items,ids,True);self.assertEqual(chosen[-1]['args']['members'],['b1']);self.assertIn('gone',values[-1]['args']['members'])
  with self.assertRaises(Invalid):choose_recovery(items,{values[1]['intent_id']})
 def test_sequential_preview_counts(self):
  values=[intent('add_members','c2',dict(members=['b0','b1'])),intent('remove_members','c2',dict(members=['b0']))]
  ops=plan(self.s,values,['c2'],'library').data['operations'];rows=plan_rows(ops,Catalog(self.s),dict(add_members='加入',remove_members='移除'))
  self.assertIn('0 本 → 加入 2 本 → 2 本',rows[0]['cells'][2]);self.assertIn('2 本 → 移除 1 本 → 1 本',rows[1]['cells'][2])
 def test_status_distinguishes_writeback(self):
  req=make_request();job=dict(request=req,status='staged',publication_verified=True)
  state=SimpleNamespace(column_pending=lambda _:None,baseline_done=lambda _:set())
  self.assertIn('等待',task_status(job,state)[0]);job['result']=receipt(req)
  self.assertEqual(task_status(job,state)[0],'设备执行完成')
  state.baseline_done=lambda _:{o['op_id'] for o in req['operations']}
  self.assertEqual(task_status(job,state)[0],'执行及列值处理完成')
  state.column_pending=lambda _:{'rows':[]}
  self.assertIn('待处理',task_status(job,state)[0])
 def logs(self):
  store=mount_fixture(self.root/'mount',self.s) if (self.root/'mount').exists() else None
  return store
 def setup_storage(self):
  mount=self.root/'mount';mount.mkdir();store=mount_fixture(mount,self.s);store.path('logs').mkdir()
  for i in range(13):
   p=store.path(f'logs/KC运行-{i}.txt');p.write_text('log');os.utime(p,(time.time()-86400*(40+i),)*2)
  store.path('logs/personal.txt').write_text('keep');return store
 def test_cleanup_retains_evidence_and_newest_five(self):
  store=self.setup_storage();items=cleanup_plan(store,[]);self.assertEqual(len(items),8)
  self.assertTrue(inventory(store,self.state.directory));self.assertEqual(cleanup(store,[],items),8)
  self.assertEqual(len(list(store.path('logs').glob('KC*.txt'))),5);self.assertTrue(store.path('logs/personal.txt').exists())
 def test_cleanup_rechecks_preview_and_pending(self):
  store=self.setup_storage();items=cleanup_plan(store,[]);store.path(items[0]['path']).write_text('changed')
  with self.assertRaises(Invalid):cleanup(store,[],items)
  self.assertEqual(len(list(store.path('logs').glob('KC*.txt'))),13)
  store.path('state/pending.json').write_text('{}')
  with self.assertRaises(Invalid):cleanup_plan(store,[])
 def test_cleanup_unfinished_external_task_blocks(self):
  store=self.setup_storage();store.path('inbox/unknown.json').write_bytes(canonical(make_request()))
  with self.assertRaises(Invalid):cleanup_plan(store,[])

if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Usability));raise SystemExit(not result.wasSuccessful())
