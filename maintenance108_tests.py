import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from plugin.storage import inventory
from plugin.column_io import apply
from calibre.db.legacy import LibraryDatabase
class Checks(unittest.TestCase):
 def test_inventory_no_overlap(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/'device';local=Path(t)/'local';local.mkdir();(local/'data').write_bytes(b'x'*10)
   for name,size in [('state/backups/a.db',100),('state/jobs/task/op.json',20),('state/device.json',5)]:
    p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'x'*size)
   rows={n:(c,s) for n,c,s in inventory(SimpleNamespace(path=lambda x:root/x),local)}
   self.assertEqual(rows['数据库备份'],(1,100));self.assertEqual(rows['任务执行记录'],(1,20));self.assertEqual(rows['配置与其他执行状态'],(1,5));self.assertEqual(sum(s for c,s in rows.values()),135)
 def test_arbitrary_column_name(self):
  from calibre.ebooks.metadata.book.base import Metadata
  with tempfile.TemporaryDirectory() as t:
   db=LibraryDatabase(str(Path(t)/'library'));db.create_custom_column('my_shelves','任意书架名','text',is_multiple=True);db.close();db=LibraryDatabase(str(Path(t)/'library'))
   try:
    api=db.new_api;i=api.create_book_entry(Metadata('测试书'));api.set_field('#my_shelves',{i:['甲']})
    rows=[dict(id=i,uuid=api.field_for('uuid',i),title='测试书',before=['甲'],after=['甲','乙'])]
    apply(api,str(db.library_id),'#my_shelves',rows,Path(t)/'backups')
    self.assertEqual(set(api.field_for('#my_shelves',i)),{'甲','乙'})
   finally:db.close()
if __name__=='__main__':
 suite=unittest.TestLoader().loadTestsFromTestCase(Checks)
 for name in ['runtime_versions_tests','usability020_tests','retention021_tests']:
  suite.addTests(unittest.TestLoader().loadTestsFromName(name))
 result=unittest.TextTestRunner().run(suite)
 if not result.wasSuccessful():raise SystemExit(1)
