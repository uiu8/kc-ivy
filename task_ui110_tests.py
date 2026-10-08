import unittest
from types import SimpleNamespace
from calibre.gui2 import Application
from qt.core import QWidget,QPushButton,QFontDatabase
from plugin.task_ui import TaskHistoryDialog,first_use_dialog,operation_rows
app=Application([])
for p in ['C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/simsun.ttc']:QFontDatabase.addApplicationFont(p)
class Screens(unittest.TestCase):
 def setUp(self):
  self.calls=[];self.host=QWidget();self.host.busy=False;self.host.intents=[]
  state=SimpleNamespace(column_pending=lambda jid:False,baseline_done=lambda jid:[])
  self.host.service=SimpleNamespace(state=state)
  for name in ['check_task','recover_task_draft','retry_task','cancel_unpublished','install_device','load','probe_device','configure_rules','import_column']:
   setattr(self.host,name,lambda *args,n=name:self.calls.append((n,args)))
  self.addCleanup(self.host.close)
 def job(self,jid,confirmed=False):
  ops=[dict(op_id='a',kind='create_collection',collection_uuid='new-id',args=dict(name='新建书架')),dict(op_id='b',kind='add_members',collection_uuid='new-id',args=dict(members=['book']))]
  j=dict(request=dict(job_id=jid,operations=ops),snapshot=dict(collections=[]),status='complete' if confirmed else 'staged',column_field='')
  if confirmed:j['result']=dict(operations=[dict(op_id=o['op_id'],status='confirmed',message='Exact state, counts and existing file sizes verified') for o in ops])
  return j
 def test_selection_actions_and_names(self):
  d=TaskHistoryDialog(self.host,[self.job('first-task',True),self.job('second-task')],'second-task');d.show();app.processEvents()
  self.assertEqual(d.tasks.currentRow(),1);self.assertTrue(d.buttons['retry'].isEnabled());self.assertEqual(d.operations.item(1,1).text(),'新建书架')
  d.buttons['retry'].click();self.assertEqual(self.calls,[('retry_task',('second-task',))]);d.close()
 def test_completed_and_empty(self):
  d=TaskHistoryDialog(self.host,[self.job('first-task',True)]);d.show();app.processEvents()
  self.assertFalse(d.buttons['retry'].isEnabled());self.assertFalse(d.buttons['recover'].isEnabled());self.assertIn('已核对',d.operations.item(0,4).text());self.assertIn('Exact state',d.raw.toPlainText())
  d.grab().save('dist/task-history-1.0.10.png');d.close()
  d=TaskHistoryDialog(self.host,[]);self.assertTrue(all(not b.isEnabled() for b in d.buttons.values()));d.close()
 def test_guide_callbacks(self):
  d=first_use_dialog(self.host);d.show();app.processEvents();d.grab().save('dist/first-use-1.0.10.png')
  next(b for b in d.findChildren(QPushButton) if b.text()=='同步设置').click();self.assertEqual(self.calls,[('configure_rules',())]);d.close()
if __name__=='__main__':
 suite=unittest.TestLoader().loadTestsFromTestCase(Screens)
 suite.addTests(unittest.TestLoader().loadTestsFromName('usability020_tests'))
 r=unittest.TextTestRunner().run(suite)
 if not r.wasSuccessful():raise SystemExit(1)
