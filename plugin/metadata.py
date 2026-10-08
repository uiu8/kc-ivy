"""Bulk Calibre metadata only. Never reads ebook formats/covers."""
from collections import defaultdict
from datetime import datetime,date
from .protocol import Invalid, checkpoint, digest

BUILTINS=('authors','author_sort','publisher','series','tags','title','timestamp','languages','rating')


def value_list(value, datatype='text', label=''):
    if value is None or value is False: return []
    if datatype=='bool': return [label] if value else []
    if isinstance(value,(tuple,list,set)):
        return [str(x) for x in value if x is not None and str(x).strip()]
    if isinstance(value,(datetime,date)): return [value.strftime('%Y-%m-%d')]
    if datatype=='rating': return [format(float(value)/2,'g')+' Stars'] if value else []
    return [str(value)] if str(value).strip() else []


def read_metadata(api, fields):
    requested=set(fields)|{'uuid','title','authors'}
    result={}
    with api.safe_read_lock:
        ids=api.all_book_ids()
        from .book_links import links,attach
        saved_links=links(api)
        categories=api.pref('user_categories',{})
        all_categories='user_categories' in requested
        requested.discard('user_categories')
        if all_categories:requested.update('@'+name for name in categories)
        category_fields={field for field in requested if field.startswith('@')}
        requested.difference_update(category_fields)
        for field in category_fields:
            name=field[1:]
            if name not in categories:raise Invalid('用户分类不存在：'+name)
            requested.update(entry[1] for entry in categories[name])
        for field in sorted(requested):
            checkpoint()
            if field not in api.field_metadata: raise Invalid('来源字段不存在：'+field)
            meta=api.field_metadata[field]
            values=api.all_field_for(field,ids)
            if field=='languages':
                from calibre.utils.localization import calibre_langcode_to_name
                values={i:[calibre_langcode_to_name(v) for v in languages] for i,languages in values.items()}
            result[field]={str(i):value_list(values.get(i),meta.get('datatype','text'),meta.get('name',field)) for i in ids}
        # User categories are configuration, not arbitrary device-supplied code.
        for field in category_fields:
            name=field[1:];entries=categories[name]
            result[field]={str(i):[name] if any(str(e[0]) in result[e[1]][str(i)] for e in entries) else [] for i in ids}
        requested.update(category_fields)
        if all_categories:
            result['user_categories']={str(i):[name for name in categories if result['@'+name][str(i)]] for i in ids}
            requested.add('user_categories')
    rows=[]
    for i in sorted(ids):
        sid=str(i)
        rows.append(dict(id=i,uuid=(result['uuid'][sid] or [''])[0],
                         title=(result['title'][sid] or [''])[0],fields={f:result[f][sid] for f in sorted(requested)}))
    attach(rows,saved_links)
    return dict(rows=rows,user_categories=categories,fingerprint=digest(dict(schema='kc-metadata-fingerprint/v1',rows=rows,categories=categories)))


def mappings(snapshot, rows):
    from .book_links import device_key,book_identity
    device=device_key(snapshot);explicit=defaultdict(set)
    byuuid=defaultdict(list)
    for row in rows:
        byuuid[row['uuid']].append(row)
        for link in row.get('device_links',[]):
            if link['device']==device:explicit[tuple(link['identity'])].add(row['uuid'])
    copies=defaultdict(list)
    for book in snapshot['books']:
        uid=book['calibre_uuid'] if snapshot['mapping']['stable'] else None
        linked=explicit.get(tuple(book_identity(book)),set())
        if len(linked)>1:continue
        if len(linked)==1:
            target=next(iter(linked))
            # A conflicting automatic association is not silently overridden.
            if not uid or uid==target or uid not in byuuid:uid=target
            else:continue
            if len(byuuid.get(uid,[]))==1:copies[uid].append(book['uuid'])
            continue
        if snapshot['mapping']['stable'] and uid and book['metadata_matches']==1 and len(byuuid.get(uid,[]))==1:
            copies[uid].append(book['uuid'])
    return {uid:sorted(ids) for uid,ids in copies.items()}


def assert_library(snapshot, library, metadata=None):
    marker=snapshot['mapping']['calibre_library_uuid']
    if marker and marker!=library:
        from .readiness import library_context
        raise Invalid('快照属于另一个 Calibre 书库。\n'+library_context(snapshot,library))
    if not snapshot['mapping']['stable'] and not (metadata and mappings(snapshot,metadata['rows'])):
        raise Invalid('设备自动映射不可用；请刷新设备状态，或先为已索引书籍建立手动书库绑定。')
