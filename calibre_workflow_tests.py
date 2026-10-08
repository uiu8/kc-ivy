import pathlib
if not __debug__:
    exec(compile(pathlib.Path(__file__).read_text(encoding='utf-8'),__file__,'exec',optimize=0),globals());raise SystemExit(0)
import tempfile,sys,json
from pathlib import Path
from calibre.db.legacy import LibraryDatabase
from calibre.ebooks.metadata.book.base import Metadata
from plugin.service import Service
from plugin import column_io
from plugin.metadata import read_metadata,mappings
from plugin.planner import intent
from plugin.protocol import Invalid
from tests import snapshot,receipt,mount_fixture

ROOT=Path(__file__).resolve().parent;checks=[]
with tempfile.TemporaryDirectory(prefix='.ct-',dir=ROOT.parents[1]) as temp:
    root=Path(temp);db=LibraryDatabase(str(root/'library'))
    db.create_custom_column('shelf','Kindle书架','text',is_multiple=True)
    for label,kind in [('summary','text'),('longsummary','comments'),('has_shelf','bool')]:db.create_custom_column(label,label,kind,is_multiple=False)
    db.create_custom_column('computed','模板来源','composite',is_multiple=False,display={'composite_template':'{title}'});db.close()
    db=LibraryDatabase(str(root/'library'));api=db.new_api
    ids=[api.create_book_entry(Metadata('书'+str(i),['作者'])) for i in range(3)]
    api.set_field('#shelf',{ids[0]:['待读'],ids[1]:['待读']})
    s=snapshot();library=str(db.library_id)
    for b,i in zip(s['books'],ids):b['calibre_uuid']=api.field_for('uuid',i)
    mount=root/'mount';mount.mkdir();store=mount_fixture(mount,s)
    svc=Service(root/'private');m=read_metadata(api,['#shelf'])
    p=svc.adopt(api,library,s,'#shelf',mappings(s,m['rows']))
    checks.append('real Calibre UUID adoption and bulk metadata')
    api.set_pref('user_categories',{'测试分类':[['作者','authors',0]]})
    sources=read_metadata(api,['#computed','user_categories','@测试分类','authors','author_sort','publisher','series','tags','title','timestamp','languages','rating'])
    assert all(r['fields']['user_categories']==['测试分类'] for r in sources['rows'])
    assert all(r['fields']['#computed']==[r['title']] for r in sources['rows'])
    checks.append('all nine builtin sources, actual Calibre composite template and User Categories via real bulk API')
    api.set_field('#shelf',{ids[0]:['文学']})
    value=svc.preview(api,library,s,[])
    assert value['plan'].ready and {o['kind'] for o in value['plan'].data['operations']}=={'add_members','remove_members'}
    jid=svc.submit(api,library,store,value,[]);j=svc.state.job(jid)
    result=receipt(j['request']);p,messages=svc.receive(library,s,[result])
    assert all('文学' in b['values'] for uid,b in p['column_baseline'].items() if uid==s['books'][0]['calibre_uuid'])
    checks.append('direct column edits -> same Preview -> USB task -> durable confirmed baseline')
    # A genuine write and restore, preserving books not in the write batch.
    rows=[dict(id=ids[0],uuid=api.field_for('uuid',ids[0]),title='书0',before=['文学'],after=['文学','新架'])]
    backup,count=column_io.apply(api,library,'#shelf',rows,root/'backups')
    assert count==1 and set(api.field_for('#shelf',ids[0]))=={'文学','新架'}
    assert api.field_for('#shelf',ids[1])==('待读',)
    column_io.restore(api,library,backup,root/'backups')
    assert api.field_for('#shelf',ids[0])==('文学',)
    checks.append('actual Calibre batch write + readback + backup + conflict-aware restore')
    for field in ('#summary','#longsummary','#has_shelf'):
        rows=column_io.import_summary_plan(s,read_metadata(api,[]),api,field)
        backup,count=column_io.apply(api,library,field,rows,root/'backups',summary=True)
        assert count>0 and api.field_for(field,ids[0])
        column_io.restore(api,library,backup,root/'backups')
        assert not api.field_for(field,ids[0])
    checks.append('single text, long text, bool summary imports and restoration without roundtrip authority')
    stale=svc.preview(api,library,s,[]);api.set_field('#shelf',{ids[2]:['后来编辑']})
    try:svc.submit(api,library,store,stale,[])
    except Invalid:pass
    else:raise AssertionError('stale metadata was sent')
    checks.append('metadata edit invalidates immutable Preview before USB publication')
    db.close()
(ROOT/'calibre-workflow-results.json').write_text(json.dumps(dict(passed=True,checks=checks,real_device=False),ensure_ascii=False,indent=2),encoding='utf8')
print('PASS',checks)
