"""Notice guidance, selection and disclosure checks with offscreen Qt renders."""
import unittest
from pathlib import Path
from calibre.gui2 import Application
from qt.core import QWidget,QLabel,QFont,QFontDatabase,QTableWidget,QTextBrowser,QPushButton,QTabWidget,Qt,QApplication
from plugin.notice_ui import notice_dialog
from plugin.readiness import file_guidance
from tests import snapshot

app=Application([])
for font in ('C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/simsun.ttc'):QFontDatabase.addApplicationFont(font)
app.setFont(QFont('Microsoft YaHei',10))
out=Path('dist/release-1.0.23');out.mkdir(parents=True,exist_ok=True)

class Notice(unittest.TestCase):
    def test_guidance_distinguishes_directory_and_task_evidence(self):
        b=dict(uuid='kc-new-one',title='Dictionary',location='/mnt/us/documents/dictionaries/dict.azw')
        self.assertIn('可先不管',file_guidance(b)[0])
        job=dict(request=dict(new_books=[dict(alias=b['uuid'])],operations=[dict(op_id='one',args=dict(members=[b['uuid']]))]))
        self.assertEqual(file_guidance(b,[job])[0],'到 Kindle 执行任务')
        job['result']=dict(operations=[dict(op_id='one',status='pending')])
        self.assertEqual(file_guidance(b,[job])[0],'先核对原任务')
        b['location']='/mnt/us/documents/Downloads/Dictionary.azw'
        self.assertNotIn('词典目录',file_guidance(b)[0])

    def test_selection_disclosure_and_diagnostics(self):
        h=QWidget();h.snapshot=snapshot();h.snapshot['books']=[
            dict(uuid='kc-new-one',title='第一卷我 <新书>',location='/mnt/us/documents/Wei Zhi/'+('long path '*15)+'book.mobi'),
            dict(uuid='kc-new-two',title='Chinese_English_Dictionary',location='/mnt/us/documents/dictionaries/dict.azw')]
        h.service=None;h.library_id='test-library';h.mode_summary=QLabel();h.mode_summary.setToolTip('联合整理 · Kindle书架\n同步列：Kindle书架（#kindlecollections）')
        h.status=QLabel('状态生成于 2026-10-08\n外部任务回执：example-job\n已清理 0 个已完成任务；保留诊断记录\n其他错误必须保留')
        h.task_history=lambda _:None
        d=notice_dialog(h);d.show();tabs=d.findChild(QTabWidget);tabs.setCurrentIndex(1);app.processEvents()
        table=d.findChild(QTableWidget,'pending_files');detail=d.findChild(QTextBrowser,'file_explanation')
        self.assertEqual(table.columnCount(),3);self.assertEqual(table.rowCount(),2)
        table.sortItems(0,Qt.SortOrder.DescendingOrder)
        for row in range(2):
            table.selectRow(row);app.processEvents()
            self.assertIn(table.item(row,0).text(),detail.toPlainText())
            if table.item(row,1).text()=='AZW':self.assertIn('如果只是用它查词',detail.toPlainText())
            else:self.assertIn('可以先在 KC++ 加入收藏夹',detail.toPlainText())
        d.findChild(QPushButton,'file_path_toggle').click();app.processEvents()
        self.assertTrue(d.findChild(QLabel,'file_path').isVisible())
        d.grab().save(str(out/'pending-files.png'))
        tabs.setCurrentIndex(2);app.processEvents()
        summary=d.findChild(QTextBrowser,'diagnostic_summary').toPlainText()
        self.assertIn('其他来源的任务结果',summary);self.assertIn('其他错误必须保留',summary)
        technical=d.findChild(QTextBrowser,'raw_diagnostics');self.assertFalse(technical.isVisible())
        d.grab().save(str(out/'diagnostics.png'))
        d.findChild(QPushButton,'diagnostic_toggle').click();app.processEvents();self.assertTrue(technical.isVisible())
        self.assertIn('example-job',technical.toPlainText())
        next(b for b in d.findChildren(QPushButton) if b.text()=='复制诊断信息').click()
        self.assertEqual(QApplication.clipboard().text(),technical.toPlainText())
        d.close();h.close()

r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Notice))
if not r.wasSuccessful():raise SystemExit(1)
