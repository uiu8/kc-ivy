"""Explicit library-owned links to native Kindle entries, never title matching."""
from copy import deepcopy
from .protocol import Invalid, digest
from .deferred import pending

PREF='kc_plus_device_book_links_v1'

def device_key(snapshot):
    return digest(snapshot['device'])

def links(api):
    return deepcopy(api.pref(PREF, {}))

def book_identity(book):
    return [book['uuid'],book['location'],book.get('cde_key'),book.get('cde_type')]

def attach(rows, saved):
    by_uuid={r['uuid']:r for r in rows}
    for device, entries in saved.items():
        for entry in entries.values():
            row=by_uuid.get(entry['calibre_uuid'])
            if row is not None:row.setdefault('device_links',[]).append(dict(entry,device=device))

def bind(api,snapshot,book_uuid,record_id):
    from .metadata import read_metadata,mappings
    book=next((b for b in snapshot['books'] if b['uuid']==book_uuid),None)
    if book is None or pending(book_uuid):raise Invalid('请先在 Kindle 索引此书并刷新状态，再建立书库绑定。')
    if record_id not in api.all_book_ids():raise Invalid('书库记录已不存在，请重新选择。')
    target=api.field_for('uuid',record_id)
    current=mappings(snapshot,read_metadata(api,[])['rows'])
    if any(book_uuid in copies and uid!=target for uid,copies in current.items()):
        raise Invalid('此书已关联另一条书库记录。手动绑定请先解除；自动匹配请保留原记录。')
    saved=links(api);entries=saved.setdefault(device_key(snapshot),{})
    entries[book_uuid]=dict(identity=book_identity(book),calibre_uuid=target)
    api.set_pref(PREF,saved)

def unlink(api,snapshot,book_uuid):
    saved=links(api);entries=saved.get(device_key(snapshot),{})
    if book_uuid not in entries:raise Invalid('此书没有手动绑定；自动匹配不能在这里解除。')
    del entries[book_uuid]
    api.set_pref(PREF,saved)

def create_record(api,snapshot,book_uuid,title,authors):
    from calibre.ebooks.metadata.book.base import Metadata
    from .metadata import read_metadata,mappings
    book=next((b for b in snapshot['books'] if b['uuid']==book_uuid),None)
    if book is None or pending(book_uuid):raise Invalid('请先索引并刷新状态，再创建对应记录。')
    if any(book_uuid in ids for ids in mappings(snapshot,read_metadata(api,[])['rows']).values()):
        raise Invalid('此书已有对应记录，请使用现有记录。')
    if not title.strip():raise Invalid('请填写书名。')
    ident=api.create_book_entry(Metadata(title.strip(),authors or ['未知']))
    # Keep a successfully created record if saving the link fails; never delete user data.
    bind(api,snapshot,book_uuid,ident)
    return ident

def link_report(api,snapshot):
    from .metadata import read_metadata,mappings
    rows=read_metadata(api,[])['rows'];records={r['uuid']:r for r in rows}
    books={b['uuid']:b for b in snapshot['books']};active=mappings(snapshot,rows);result=[]
    for uid,entry in links(api).get(device_key(snapshot),{}).items():
        record=records.get(entry['calibre_uuid']);book=books.get(uid)
        reason=('书库记录已删除' if not record else '设备编号不在当前快照' if not book else
                '路径或内容标识已变化' if book_identity(book)!=entry['identity'] else
                '与设备自动关联冲突' if uid not in active.get(entry['calibre_uuid'],[]) else '')
        result.append(dict(uuid=uid,entry=entry,reason=reason,title=record['title'] if record else entry['identity'][1] or uid))
    return result

def repair(api,snapshot,old_uuid,new_uuid):
    from .metadata import read_metadata,mappings
    saved=links(api);entries=saved.get(device_key(snapshot),{});old=entries.get(old_uuid)
    if not old:raise Invalid('原绑定已变化，请重新读取。')
    book=next((b for b in snapshot['books'] if b['uuid']==new_uuid),None)
    if book is None or pending(new_uuid):raise Invalid('请选择已取得原生编号的设备书籍。')
    rows=read_metadata(api,[])['rows'];target=old['calibre_uuid']
    if not any(r['uuid']==target for r in rows):raise Invalid('原书库记录已删除；请解除旧绑定，再选择已有记录或新建记录。')
    if any(new_uuid in ids and uid!=target for uid,ids in mappings(snapshot,rows).items()):raise Invalid('目标书籍已关联另一书库记录。')
    del entries[old_uuid];entries[new_uuid]=dict(identity=book_identity(book),calibre_uuid=target)
    api.set_pref(PREF,saved)
