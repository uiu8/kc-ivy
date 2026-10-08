"""Real Calibre database and Qt form; isolated library only."""
import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from calibre.gui2 import Application
from calibre.db.legacy import LibraryDatabase
from qt.core import QWidget,QFont,QFontDatabase
from plugin.column_create import CreateShelfColumn,create_column
from plugin.protocol import Invalid
app=Application([])
for font in ('C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/simsun.ttc'):QFontDatabase.addApplicationFont(font)
app.setFont(QFont('Microsoft YaHei',10))
class ColumnCreation(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.folder=Path(self.tmp.name)/'library'
        self.db=LibraryDatabase(str(self.folder));self.host=QWidget()
        self.host.library_db=self.db;self.host.gui=SimpleNamespace(must_restart_before_config=False)
        self.host.busy=False;self.host.same_library=lambda:True
    def tearDown(self):
        self.host.close();self.db.close();self.tmp.cleanup()
    def test_form_create_reopen_and_write_multiple_values(self):
        dialog=CreateShelfColumn(self.host);dialog.resize(540,470);dialog.show();app.processEvents()
        out=Path('dist/release-1.0.17');out.mkdir(parents=True,exist_ok=True)
        dialog.grab().save(str(out/'create-column.png'))
        dialog.lookup.setText('my_shelves');dialog.title.setText('自定书架');dialog.create.click()
        self.assertFalse(dialog.create.isEnabled());self.assertTrue(self.host.gui.must_restart_before_config)
        self.assertIn('已创建',dialog.notice.text());dialog.close()
        self.db.close();self.db=LibraryDatabase(str(self.folder))
        field=self.db.field_metadata['#my_shelves']
        self.assertEqual(field['name'],'自定书架');self.assertEqual(field['datatype'],'text');self.assertTrue(field['is_multiple'])
        from calibre.ebooks.metadata.book.base import Metadata
        api=self.db.new_api;book=api.create_book_entry(Metadata('测试书'))
        api.set_field('#my_shelves',{book:['甲','乙']})
        self.assertEqual(set(api.field_for('#my_shelves',book)),{'甲','乙'})
    def test_cancel_invalid_duplicate_restart_and_context(self):
        dialog=CreateShelfColumn(self.host);dialog.reject()
        self.assertNotIn('#bookshelves',self.db.field_metadata)
        for name in ('bad name','9shelf','UPPER'):
            with self.assertRaises(Invalid):create_column(self.host,name,'标题')
        self.host.same_library=lambda:False
        with self.assertRaises(Invalid):create_column(self.host,'myshelf','标题')
        self.host.same_library=lambda:True
        self.assertEqual(create_column(self.host,'#mine','专用列'),'#mine')
        with self.assertRaises(Invalid):create_column(self.host,'again','另一列')
        self.db.close();self.db=LibraryDatabase(str(self.folder));self.host.library_db=self.db
        self.host.gui.must_restart_before_config=False
        with self.assertRaises(Invalid):create_column(self.host,'mine','新标题')
        with self.assertRaises(Invalid):create_column(self.host,'another','专用列')
r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ColumnCreation))
if not r.wasSuccessful():raise SystemExit(1)
