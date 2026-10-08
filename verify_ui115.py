"""Installed Qt settings: explicit first choice and persisted three-way configuration."""
import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from calibre.gui2 import Application
from calibre.db.legacy import LibraryDatabase
from qt.core import QWidget,QComboBox,QLabel,QTabWidget,QTimer,QApplication,QDialogButtonBox,QFont,QFontDatabase
from calibre_plugins.kc_plus.ui import Manager
from calibre_plugins.kc_plus.pages import QMessageBox
from calibre_plugins.kc_plus.service import Service
from calibre_plugins.kc_plus.metadata import read_metadata
from calibre_plugins.kc_plus.column_io import shelf_fields
from calibre_plugins.kc_plus.planner import Catalog
from tests import snapshot

app=Application([])
for font in ('C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/simsun.ttc'):QFontDatabase.addApplicationFont(font)
app.setFont(QFont('Microsoft YaHei',10))
out=Path('dist/release-1.0.15');out.mkdir(parents=True,exist_ok=True)

class Settings(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        db=LibraryDatabase(str(self.root/'library'))
        db.create_custom_column('myshelves','我的分类架','text',is_multiple=True);db.close()
        self.db=LibraryDatabase(str(self.root/'library'));self.api=self.db.new_api
        self.gui=QWidget();self.gui.current_db=self.db;self.gui.device_manager=SimpleNamespace(is_device_present=False)
        self.host=Manager(self.gui);self.host.service=Service(self.root/'state');self.s=snapshot()
        self.host.loaded((self.s,[],Catalog(self.s),[],[]));self.host.show();app.processEvents()

    def tearDown(self):
        self.host.close();self.gui.close();self.db.close();self.tmp.cleanup()

    def profile(self):return self.host.service.profile(str(self.db.library_id),self.s)

    def dialog(self,action,fields=None):
        fields=shelf_fields(self.api) if fields is None else fields
        errors=[]
        def interact():
            d=QApplication.activeModalWidget()
            try:action(d)
            except BaseException as exc:errors.append(exc)
            finally:
                if d.isVisible():d.reject()
        QTimer.singleShot(30,interact)
        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes),patch.object(self.host,'background',side_effect=lambda fn,done:done(fn())):
            self.host.open_sync_settings(self.profile(),read_metadata(self.api,fields),fields)
            app.processEvents()
        if errors:raise errors[0]

    def save(self,d):d.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()

    def test_unconfigured_no_automatic_column_and_explicit_tags(self):
        def choose(d):
            mode=d.findChild(QComboBox,'sync_mode');column=d.findChild(QComboBox,'bookshelf_column')
            self.assertEqual(mode.currentData(),'custom');self.assertEqual(column.currentData(),'')
            self.save(d);self.assertTrue(d.isVisible());self.assertEqual(self.profile()['field'],'')
            d.grab().save(str(out/'first-choice.png'))
            mode.setCurrentIndex(mode.findData('tags'))
            self.assertFalse(column.isVisible());self.assertTrue(d.findChild(QLabel,'tags_writeback_notice').isVisible())
            d.grab().save(str(out/'tags-choice.png'));self.save(d)
        self.dialog(choose)
        self.assertEqual(self.profile()['field'],'tags');self.assertEqual(self.profile()['sync_mode'],'column')
        self.assertIn('原生标签',self.host.mode_summary.text())
        self.host.grab().save(str(out/'main-mode.png'))
        def reopened(d):self.assertEqual(d.findChild(QComboBox,'sync_mode').currentData(),'tags')
        self.dialog(reopened)

    def test_custom_name_manual_mode_and_saved_choice(self):
        def custom(d):
            column=d.findChild(QComboBox,'bookshelf_column');column.setCurrentIndex(column.findData('#myshelves'));self.save(d)
        self.dialog(custom)
        self.assertEqual(self.profile()['field'],'#myshelves');self.assertIn('我的分类架',self.host.mode_summary.text())
        def manual(d):
            mode=d.findChild(QComboBox,'sync_mode');self.assertEqual(mode.currentData(),'custom')
            self.assertEqual(d.findChild(QComboBox,'bookshelf_column').currentData(),'#myshelves')
            mode.setCurrentIndex(mode.findData('manual'))
            self.assertFalse(d.findChild(QComboBox,'bookshelf_column').isVisible())
            tabs=d.findChild(QTabWidget,'sync_settings_tabs')
            self.assertFalse(tabs.isTabEnabled(1));self.assertFalse(tabs.isTabEnabled(2));self.assertTrue(tabs.isTabEnabled(3))
            d.grab().save(str(out/'manual-choice.png'));self.save(d)
        self.dialog(manual)
        persisted=Service(self.root/'state').profile(str(self.db.library_id),self.s)
        self.assertEqual(persisted['sync_mode'],'manual');self.assertEqual(persisted['field'],'#myshelves')
        self.assertEqual(self.host.mode_summary.text(),'仅 KC++ 整理')
        def reopened(d):self.assertEqual(d.findChild(QComboBox,'sync_mode').currentData(),'manual')
        self.dialog(reopened)

    def test_all_scope_and_restart_guard(self):
        from qt.core import QAbstractItemView
        def choose(d):
            column=d.findChild(QComboBox,'bookshelf_column');column.setCurrentIndex(column.findData('#myshelves'))
            d.findChild(QComboBox,'scope_mode').setCurrentIndex(0)
            d.findChild(QAbstractItemView,'sync_scope_books').clearSelection()
            self.save(d)
        self.dialog(choose)
        self.assertEqual(self.profile()['scope_mode'],'all')
        revision=self.profile()['revision'];self.gui.must_restart_before_config=True
        def blocked(d):
            self.save(d);self.assertTrue(d.isVisible())
            self.assertIn('重启',d.findChild(QLabel,'missing_column_notice').text())
        self.dialog(blocked)
        self.assertEqual(self.profile()['revision'],revision)

    def test_dismiss_current_notice_but_show_new_error(self):
        text='提示忽略测试 '+self.host.library_id
        self.host.show_notice(text,'warning');app.processEvents()
        self.assertFalse(self.host.status.isHidden())
        self.host.dismiss_notice();self.assertTrue(self.host.status.isHidden())
        self.host.show_notice(text,'warning');self.assertTrue(self.host.status.isHidden())
        self.host.show_notice(text+' 新错误','error');self.assertFalse(self.host.status.isHidden())
        self.assertEqual(self.host.status.text(),text+' 新错误')

    def test_no_custom_column_still_offers_tags_without_creation(self):
        def choose(d):
            column=d.findChild(QComboBox,'bookshelf_column');self.assertEqual(column.count(),1)
            mode=d.findChild(QComboBox,'sync_mode');self.assertEqual(mode.currentData(),'custom')
            self.assertTrue(d.findChild(QLabel,'missing_column_notice').isVisible())
            mode.setCurrentIndex(mode.findData('tags'));self.save(d)
        self.dialog(choose,['tags']);self.assertEqual(self.profile()['field'],'tags')

result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Settings))
if not result.wasSuccessful():raise SystemExit(1)
