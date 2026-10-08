import pathlib
if not __debug__:
 exec(compile(pathlib.Path(__file__).read_text(encoding='utf8'),__file__,'exec',optimize=0),globals());raise SystemExit(0)
import tempfile
from pathlib import Path
from calibre.db.legacy import LibraryDatabase
from calibre.ebooks.metadata.book.base import Metadata
from plugin.service import Service
from plugin.metadata import read_metadata,mappings
from plugin.planner import intent
from tests import snapshot,receipt,mount_fixture
with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
 root=Path(tmp);db=LibraryDatabase(str(root/'library'));db.create_custom_column('shelf','Kindle书架','text',is_multiple=True);db.close();db=LibraryDatabase(str(root/'library'));api=db.new_api
 ids=[api.create_book_entry(Metadata('书'+str(i),['作者'])) for i in range(3)]
 api.set_field('#shelf',{ids[0]:['待读'],ids[1]:['待读']})
 s=snapshot();library=str(db.library_id)
 for b,i in zip(s['books'],ids):b['calibre_uuid']=api.field_for('uuid',i)
 svc=Service(root/'private');svc.adopt(api,library,s,'#shelf',mappings(s,read_metadata(api,['#shelf'])['rows']))
 mount=root/'mount';mount.mkdir();store=mount_fixture(mount,s)
 manual=[intent('add_members','c2',dict(members=['b0']))]
 value=svc.preview(api,library,s,manual);jid=svc.submit(api,library,store,value,manual);job=svc.state.job(jid)
 svc.receive(library,s,[receipt(job['request'])])
 api.set_field('#shelf',{ids[0]:['待读','后加的值']})
 changed,messages=svc.backfill_confirmed(api,library,s)
 assert changed and set(api.field_for('#shelf',ids[0]))=={'待读','文学','后加的值'},messages
 assert set(api.field_for('#shelf',ids[1]))=={'待读'}
 assert list((root/'private/column-backups').glob('*.json'))
 s['relations'].append(dict(s['relations'][0],collection_uuid='c2'))
 next_preview=svc.preview(api,library,s,[intent('remove_members','c2',dict(members=['b0']))])
 assert not next_preview['issues'],next_preview['issues']
 # Simulate a task completed by an old release without baseline bookkeeping.
 from plugin.ledger import edge_key
 p=svc.profile(library,s);p['ledger']['claims'].pop(edge_key('c2','b0'),None)
 p['column_baseline'][s['books'][0]['calibre_uuid']]['values']=['待读']
 svc.state.save(svc.key(library,s),p,p['revision'])
 with svc.state.connect() as con:con.execute('DELETE FROM baseline_applied WHERE job=?',(jid,))
 before=api.field_for('#shelf',ids[0])
 _,notes=svc.backfill_confirmed(api,library,s)
 assert api.field_for('#shelf',ids[0])==before and any('修复旧任务' in n for n in notes)
 assert not svc.preview(api,library,s,[intent('remove_members','c2',dict(members=['b0']))])['issues']

 api.set_field('#shelf',{ids[0]:['新的修改']})
 assert svc.backfill_confirmed(api,library,s)[0]==[]
 assert set(api.field_for('#shelf',ids[0]))=={'新的修改'}
 db.close()
print('PASS real Calibre automatic receipt backfill, backup, concurrent edit preservation, exactly once')

