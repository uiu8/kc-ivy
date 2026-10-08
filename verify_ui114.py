"""Installed plugin: library switching, queued menu actions and stale device jobs."""
import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from calibre.gui2 import Application
from calibre.db.legacy import LibraryDatabase
from qt.core import QWidget
from calibre_plugins.kc_plus.ui import Manager,KCPlusAction
from calibre_plugins.kc_plus.service import Service
from calibre_plugins.kc_plus.planner import Catalog,intent
from calibre_plugins.kc_plus.protocol import Invalid
from tests import snapshot

app=Application([])

class Contexts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.a=LibraryDatabase(str(self.root/'a'));self.b=LibraryDatabase(str(self.root/'b'))
        self.gui=QWidget();self.gui.current_db=self.a;self.jobs=[]
        self.gui.device_manager=SimpleNamespace(create_job=lambda fn,done,desc:self.jobs.append((fn,done)))
        self.manager=Manager(self.gui);self.manager.service=Service(self.root/'state')
        self.s=snapshot();self.manager.loaded((self.s,[],Catalog(self.s),[],[]))
        self.action=SimpleNamespace(gui=self.gui,window=self.manager)
        self.action.ensure_window=lambda:KCPlusAction.ensure_window(self.action)

    def tearDown(self):
        self.manager.busy=False;self.manager.close()
        if self.action.window and self.action.window is not self.manager:self.action.window.close()
        self.gui.close();self.a.close();self.b.close();self.tmp.cleanup()

    def test_switch_preserves_distinct_drafts_and_reopen(self):
        svc=self.manager.service
        key_a=svc.key(str(self.a.library_id),self.s);key_b=svc.key(str(self.b.library_id),self.s)
        draft_a=[intent('create_collection','a-draft',dict(name='书库 A'))]
        draft_b=[intent('create_collection','b-draft',dict(name='书库 B'))]
        svc.state.save_draft(key_b,dict(intents=draft_b,scope=[],resolutions={},issues=[]))
        self.manager.intents=draft_a;self.gui.current_db=self.b
        KCPlusAction.library_changed(self.action,self.b)
        self.assertEqual(svc.state.draft(key_a)['intents'],draft_a)
        self.assertEqual(svc.state.draft(key_b)['intents'],draft_b)
        self.assertTrue(self.manager.retired);self.assertIsNone(self.action.window)
        KCPlusAction.ensure_window(self.action);fresh=self.action.window;fresh.service=svc
        fresh.loaded((self.s,[],Catalog(self.s),[],[]))
        self.assertEqual(fresh.intents,draft_b);self.assertIs(fresh.library_db,self.b)

    def test_queued_device_job_stops_on_library_switch(self):
        called=[];self.manager.device_job(lambda:called.append('write'),lambda _:called.append('done'),'test')
        fn,done=self.jobs.pop();self.gui.current_db=self.b
        KCPlusAction.library_changed(self.action,self.b)
        with self.assertRaisesRegex(Invalid,'书库已切换'):fn()
        done(SimpleNamespace(failed=False,result=None))
        self.assertEqual(called,[])

    def test_menu_continues_once_after_refresh(self):
        with patch('calibre_plugins.kc_plus.ui.connected_store') as store,patch.object(self.manager,'create_collection') as create:
            self.manager.service=None;store.return_value.snapshot.return_value=self.s;store.return_value.receipts.return_value=[]
            KCPlusAction.open_manager(self.action,action='create_collection')
            self.assertTrue(self.manager.busy);create.assert_not_called()
            fn,done=self.jobs.pop();done(SimpleNamespace(failed=False,result=fn()))
            app.processEvents();create.assert_called_once()
            app.processEvents();create.assert_called_once()

    def test_menu_failure_and_library_switch_cancel_queued_action(self):
        with patch.object(self.manager,'create_collection') as create:
            KCPlusAction.open_manager(self.action,action='create_collection')
            _,done=self.jobs.pop();done(SimpleNamespace(failed=True,exception='device disconnected'))
            app.processEvents();create.assert_not_called();self.assertIsNone(self.manager.after_load_action)
            KCPlusAction.open_manager(self.action,action='create_collection')
            self.gui.current_db=self.b;KCPlusAction.library_changed(self.action,self.b)
            _,done=self.jobs.pop();done(SimpleNamespace(failed=False,result=(self.s,[],Catalog(self.s),[],[])))
            app.processEvents();create.assert_not_called()

result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Contexts))
if not result.wasSuccessful():raise SystemExit(1)
