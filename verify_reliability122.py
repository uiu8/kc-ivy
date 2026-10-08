"""UI lifecycle tests without touching the real library or Kindle."""
import tempfile,unittest,copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
from verify_books121 import app
from qt.core import QWidget,QTableWidget,QComboBox,QLineEdit,QLabel,QTimer,QApplication
from calibre.db.legacy import LibraryDatabase
from calibre.ebooks.metadata.book.base import Metadata
from plugin.ui import Manager
from plugin.service import Service
from plugin.planner import Catalog,intent
from plugin.protocol import canonical
from plugin.book_links_ui import BookLinksDialog,QMessageBox
from plugin import book_links
from tests import snapshot,mount_fixture

class LifecycleUI(unittest.TestCase):
    def test_share_review_lists_metadata_only_before_export(self):
        from plugin.migration_ui import MigrationCenter
        from io import BytesIO
        api=self.db.new_api;empty=api.create_book_entry(Metadata('仅元数据'))
        full=api.create_book_entry(Metadata('包含文件'));api.add_format(full,'TXT',BytesIO(b'book'))
        center=QWidget(self.host);center.api=api;center.library=str(self.db.library_id)
        center.fields=QComboBox();center.fields.addItem('标签','tags');center.share_field='tags'
        selection=SimpleNamespace(selected_books=lambda:{empty,full},relations=lambda:{},groups={})
        center.share_picker=SimpleNamespace(selection=selection);center.formats=QLineEdit('TXT');center.notice=QLabel()
        center.run=lambda fn,done:done(fn())
        seen=[]
        def approve():
            for d in QApplication.topLevelWidgets():
                if d.windowTitle()=='确认分享范围':
                    table=d.findChild(QTableWidget);seen.extend(table.item(i,2).text() for i in range(table.rowCount()))
                    d.grab().save('dist/release-1.0.22/share-review.png');d.accept();return
        QTimer.singleShot(0,approve)
        with patch('plugin.migration_ui.QFileDialog.getSaveFileName',return_value=(str(self.root/'share.zip'),'')):
            MigrationCenter.export_books(center)
        self.assertTrue(any('仅元数据' in s for s in seen));self.assertTrue((self.root/'share.zip').exists())
        self.assertIn('跳过 1 本',center.notice.text());center.close()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.db=LibraryDatabase(str(self.root/'library'));self.gui=QWidget();self.gui.current_db=self.db
        self.gui.device_manager=SimpleNamespace(is_device_present=False)
        self.gui.library_view=Mock()
        self.host=Manager(self.gui);self.host.service=Service(self.root/'state')
        self.s=snapshot();self.s['mapping']['calibre_library_uuid']=str(self.db.library_id)
        self.host.loaded((self.s,[],Catalog(self.s),[],[]))
    def tearDown(self):
        self.host.busy=False;self.host.close();self.gui.close();self.db.close();self.tmp.cleanup()
    def test_scan_progress_draft_keeps_identity_and_cancelled_scan(self):
        mount=self.root/'device';mount.mkdir();store=mount_fixture(mount,self.s);(mount/'documents').mkdir()
        (mount/'documents/new.mobi').write_bytes(b'new')
        old=store.snapshot();self.host.loaded((old,[],Catalog(old),[],[]));uid=old['books'][-1]['uuid']
        self.host.queue([intent('add_members','c2',dict(members=[uid]))])
        (mount/'metadata.calibre').write_bytes(canonical([dict(uuid='calibre-new',lpath='documents/new.mobi',title='书库识别后的标题')]))
        def create_job(fn,done,description):
            try:job=SimpleNamespace(result=fn(),failed=False)
            except Exception as e:job=SimpleNamespace(failed=True,exception=e)
            done(job)
        self.gui.device_manager.create_job=create_job
        with patch('plugin.ui.connected_store',return_value=store):self.host.load()
        self.assertFalse(self.host.busy);self.assertEqual(self.host.intents[0]['args']['members'],[uid])
        self.assertIn('book_anchors',self.host.service.state.draft(self.host.service.key(self.host.library_id,self.host.snapshot)))
        self.assertEqual(self.host.catalog.books[uid]['title'],'书库识别后的标题')
        self.host._last_scan_full=False;self.host.update_buttons();self.assertFalse(self.host.calculate.isEnabled());self.assertFalse(self.host.send.isEnabled())
    def test_stale_binding_repair_ui_and_persistence(self):
        api=self.db.new_api;record=api.create_book_entry(Metadata('原书记录'))
        self.s['books'][0].update(calibre_uuid=None,metadata_matches=0)
        uid=self.s['books'][0]['uuid'];book_links.bind(api,self.s,uid,record)
        changed=copy.deepcopy(self.s);changed['books'][0]['uuid']='replacement'
        self.host.snapshot=changed
        dialog=BookLinksDialog(self.host);dialog.show();app.processEvents()
        self.assertEqual(dialog.stale.count(),1)
        i=next(i for i,b in enumerate(dialog.books) if b['uuid']=='replacement');dialog.device_table.selectRow(i)
        out=Path('dist/release-1.0.22');out.mkdir(parents=True,exist_ok=True);dialog.grab().save(str(out/'repair-binding.png'))
        with patch('plugin.book_links_ui.QMessageBox.question',return_value=QMessageBox.StandardButton.Yes):dialog.repair_button.click()
        self.assertEqual(dialog.stale.count(),0);self.assertIn('已修复',dialog.notice.text());dialog.close()

if __name__=='__main__':
    r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(LifecycleUI))
    if not r.wasSuccessful():raise SystemExit(1)
