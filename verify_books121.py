"""Qt actions and persisted mapping with an isolated library."""
import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
from calibre.gui2 import Application
from calibre.db.legacy import LibraryDatabase
from calibre.ebooks.metadata.book.base import Metadata
from qt.core import QWidget,QFont,QFontDatabase
from plugin.book_links_ui import BookLinksDialog,QMessageBox
from plugin.metadata import read_metadata,mappings
from tests import snapshot

app=Application([])
for font in ('C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/simsun.ttc'):QFontDatabase.addApplicationFont(font)
app.setFont(QFont('Microsoft YaHei',10))

class MappingUI(unittest.TestCase):
    def test_bind_search_unlink_create_and_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=LibraryDatabase(str(Path(tmp)/'library'));api=db.new_api
            record=api.create_book_entry(Metadata('我的设备书',['测试作者']))
            host=QWidget();host.library_db=db;host.library_id=str(db.library_id);host.snapshot=snapshot()
            for b in host.snapshot['books']:b.update(calibre_uuid=None,metadata_matches=0)
            host.snapshot['books'][0]['title']='设备独有书'
            host.busy=False;host.same_library=lambda:True;host.preview='old';host.prepared='old';host.preview_ready=True
            host.plan_model=Mock();host.update_buttons=Mock()
            host.gui=SimpleNamespace(library_view=Mock(),must_restart_before_config=False)
            state=SimpleNamespace(job_summaries=lambda k:[],column_pending=lambda k:None)
            host.service=SimpleNamespace(key=lambda *args:'key',state=state)
            dialog=BookLinksDialog(host);dialog.show();app.processEvents()
            i=next(i for i,b in enumerate(dialog.books) if b['uuid']==host.snapshot['books'][0]['uuid'])
            dialog.device_table.selectRow(i);dialog.library_table.selectRow(0)
            self.assertTrue(dialog.bind_button.isEnabled())
            out=Path('dist/release-1.0.21');out.mkdir(parents=True,exist_ok=True)
            dialog.grab().save(str(out/'device-book-links.png'))
            with patch('plugin.book_links_ui.QMessageBox.question',return_value=QMessageBox.StandardButton.Yes):
                dialog.bind_button.click();self.assertIn('已保存绑定',dialog.notice.text())
                self.assertIsNone(host.preview)
                uid=api.field_for('uuid',record);self.assertIn(uid,mappings(host.snapshot,read_metadata(api,[])['rows']))
                i=next(i for i,b in enumerate(dialog.books) if b['uuid']==host.snapshot['books'][0]['uuid'])
                dialog.device_table.selectRow(i);self.assertTrue(dialog.unlink_button.isEnabled());dialog.unlink_button.click()
                self.assertEqual(mappings(host.snapshot,read_metadata(api,[])['rows']),{})
                dialog.device_table.selectRow(i)
                with patch('plugin.book_links_ui.QInputDialog.getText',side_effect=[('新建元数据书',True),('甲 & 乙',True)]):dialog.create_button.click()
                self.assertIn('已创建并绑定',dialog.notice.text())
                self.assertEqual(len(api.all_book_ids()),2)
            dialog.library_search.setText('不存在');self.assertTrue(all(dialog.library_table.isRowHidden(r) for r in range(2)))
            state.job_summaries=lambda k:[dict(job_id='unfolded',status='complete')]
            state.job=lambda k:dict(column_field='tags',result=dict(operations=[dict(op_id='op',status='confirmed')]),request=dict(operations=[dict(op_id='op',kind='add_members')]))
            state.baseline_done=lambda k:set()
            dialog.run(lambda:self.fail('must not change links before backfill'),'')
            self.assertIn('尚未回填',dialog.notice.text())
            host.same_library=lambda:False
            dialog.run(lambda:self.fail('must not mutate'),'')
            self.assertIn('已改变',dialog.notice.text())
            dialog.close();host.close();db.close()

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MappingUI))
    if not result.wasSuccessful():raise SystemExit(1)
