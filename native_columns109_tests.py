from migration_share_tests import SharingMigration
from plugin.column_io import apply,restore,shelf_fields,assert_field
from plugin.protocol import Invalid
from plugin.metadata import read_metadata,mappings
from plugin.sharing import export_share,inspect_share,import_share
import unittest
class NativeTags(SharingMigration):
 def test_tags_roundtrip(self):
  self.assertIn('tags',shelf_fields(self.api));self.assertNotIn('authors',shelf_fields(self.api));self.assertNotIn('series',shelf_fields(self.api))
  for f in ['authors','series','title']:
   with self.assertRaises(Invalid):assert_field(self.api,f)
  i=self.ids[0];self.api.set_field('tags',{i:['原标签']})
  rows=[dict(id=i,uuid=self.api.field_for('uuid',i),title='书',before=['原标签'],after=['原标签','新架'])]
  path,count=apply(self.api,self.lib,'tags',rows,self.root/'backups');self.assertEqual(count,1)
  self.assertEqual(set(self.api.field_for('tags',i)),{'原标签','新架'})
  restore(self.api,self.lib,path,self.root/'backups');self.assertEqual(self.api.field_for('tags',i),('原标签',))
  s=self.target_snapshot();m=read_metadata(self.api,['tags']);p=self.svc.adopt(self.api,self.lib,s,'tags',mappings(s,m['rows']))
  self.assertEqual(p['field'],'tags');self.svc.preview(self.api,self.lib,s,[])
 def test_tags_share_import_preserves_receiver(self):
  i=self.ids[0];self.api.set_field('tags',{i:['甲','乙']});path=self.root/'tags.kcshare.zip'
  export_share(self.api,self.lib,[i],'tags',path,['TXT']);m=inspect_share(path)
  self.assertEqual(m['backup']['source']['field'],'tags')
  from calibre.ebooks.metadata.book.base import Metadata
  target=self.target.create_book_entry(Metadata('接收书'));self.target.set_field('tags',{target:['原标签']})
  import_share(self.target,self.receiver,path,'tags',self.svc.state,reuse={m['backup']['books'][0]['id']:target})
  self.assertEqual(set(self.target.field_for('tags',target)),{'甲','乙','原标签'})
if __name__=='__main__':
 result=unittest.TextTestRunner().run(unittest.TestLoader().loadTestsFromTestCase(NativeTags))
 if not result.wasSuccessful():raise SystemExit(1)

