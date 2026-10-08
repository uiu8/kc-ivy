import unittest
from pathlib import Path
from types import SimpleNamespace
from calibre.gui2 import Application
from qt.core import QWidget,QLabel,QFont,QFontDatabase,QTableWidget,QTextBrowser
from plugin.notice_ui import notice_dialog
from plugin.usability import task_status
from tests import snapshot,make_request,receipt
app=Application([])
for font in ('C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/simsun.ttc'):QFontDatabase.addApplicationFont(font)
app.setFont(QFont('Microsoft YaHei',10))
class Notice(unittest.TestCase):
 def test_closed_history_and_uncertain_result(self):
  req=make_request();result=receipt(req);result['operations'][0]['status']='conflict'
  job=dict(request=req,result=result,status='closed')
  self.assertEqual(task_status(job,None)[0],'已关闭（历史结果）')
  result['operations'][0]['status']='pending'
  self.assertEqual(task_status(job,None)[0],'结果待核验')
 def test_pending_table_readonly_and_overview(self):
  h=QWidget();h.snapshot=snapshot();h.snapshot['books'][0].update(uuid='kc-new-abc',title='待识别的书籍',location='/mnt/us/documents/book.epub')
  h.service=None;h.library_id='test-library';h.mode_summary=QLabel();h.mode_summary.setToolTip('联合整理 · 我的书架\n列同步范围：全部已匹配书籍');h.status=QLabel('诊断信息');h.task_history=lambda _:None
  d=notice_dialog(h);d.show();app.processEvents()
  table=d.findChild(QTableWidget,'pending_files');self.assertEqual(table.rowCount(),1);self.assertEqual(table.item(0,1).text(),'EPUB')
  self.assertTrue(any('已读取书籍记录 2 项' in w.toPlainText() for w in d.findChildren(QTextBrowser)))
  out=Path('dist/release-1.0.19');out.mkdir(parents=True,exist_ok=True);d.grab().save(str(out/'notice.png'));d.close();h.close()
r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Notice))
if not r.wasSuccessful():raise SystemExit(1)
