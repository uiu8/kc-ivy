"""Previewable, backed-up Calibre writes and confirmed-only three-way backfill."""
from pathlib import Path
from uuid import uuid4
from .protocol import Invalid,canonical,digest
from .transport import atomic_write,read
from .metadata import mappings
from .ledger import validate_receipt,reconcile_column
from .planner import Catalog


def is_shelf_field(field,meta):
    return bool(meta and (field=='tags' or field.startswith('#')) and meta.get('datatype')=='text' and meta.get('is_multiple'))


def shelf_fields(api):
    # Keep existing custom-column defaults; offer built-in tags explicitly.
    return sorted((k for k,m in api.field_metadata.items() if is_shelf_field(k,m)),key=lambda k:(not k.startswith('#'),k))


def field_label(api,field):
    return ('原生列 · ' if field=='tags' else '自定义列 · ')+api.field_metadata[field].get('name',field)+' ('+field+')'


def assert_field(api,field):
    if not is_shelf_field(field,api.field_metadata.get(field)):
        raise Invalid('书架往返同步支持原生“标签”或多值文本自定义列；作者、丛书等请用于分类规则')


def import_plan(snapshot,metadata,field,replace=False,bindings=None):
    cat=Catalog(snapshot); copies=mappings(snapshot,metadata['rows']); rows=[]
    aliases={}
    for name,cid in (bindings or {}).items():
        if cid in cat.collections and ',' not in name and name==cat.collections[cid]['name'].replace(',',';'):
            aliases[cid]=name
    for row in metadata['rows']:
        ids=copies.get(row['uuid'])
        if not ids: continue
        names={aliases.get(cid,cat.collections[cid]['name']) for bid in ids for cid in cat.book_collections[bid] if cid in cat.collections}
        before=sorted(set(row['fields'][field])); after=sorted(names if replace else names|set(before))
        rows.append(dict(id=row['id'],uuid=row['uuid'],title=row['title'],before=before,after=after))
    return rows


def equal(a,b):
    if isinstance(a,(list,tuple)) or isinstance(b,(list,tuple)):return sorted(a or ())==sorted(b or ())
    if a in ('',None) and b in ('',None):return True
    return type(a)==type(b) and a==b

def import_summary_plan(snapshot,metadata,api,field):
    cat=Catalog(snapshot);copies=mappings(snapshot,metadata['rows']);kind=api.field_metadata[field]['datatype']
    values=api.all_field_for(field,{row['id'] for row in metadata['rows']});result=[]
    for row in metadata['rows']:
        bids=copies.get(row['uuid'])
        if not bids:continue
        names=sorted({cat.collections[c]['name'] for bid in bids for c in cat.book_collections[bid] if c in cat.collections})
        after=bool(names) if kind=='bool' else ' | '.join(names)
        result.append(dict(id=row['id'],uuid=row['uuid'],title=row['title'],before=values.get(row['id']),after=after))
    return result

def apply(api,library,field,rows,backup_dir,summary=False):
    if summary:
        meta=api.field_metadata.get(field,{})
        if not field.startswith('#') or meta.get('datatype') not in ('text','comments','bool') or meta.get('is_multiple'):raise Invalid('摘要仅支持单值文本、长文本和布尔自定义列')
    else:assert_field(api,field)
    changed=[r for r in rows if not equal(r['before'],r['after'])]
    if not changed: return None,0
    if len({r['id'] for r in changed})!=len(changed) or len({r['uuid'] for r in changed})!=len(changed):
        raise Invalid('回填记录身份重复')
    for row in changed if not summary else []:
        if any(',' in v or not v.strip() or '\n' in v for v in row['after']):
            raise Invalid('名称不能无损写入此多值列，请先为逗号/换行名称建立映射')
    with api.write_lock:
        ids={r['id'] for r in changed}
        uuids=api.all_field_for('uuid',ids); values=api.all_field_for(field,ids)
        names={v.casefold():v for v in api.all_field_names(field)} if api.field_metadata[field]['datatype']=='text' else {}
        for row in changed:
            if uuids.get(row['id'])!=row['uuid'] or not equal(values.get(row['id']),row['before']):
                raise Invalid('回填预览已过期，书籍身份或列值已改变')
            for value in ([row['after']] if summary and isinstance(row['after'],str) else [] if summary else row['after']):
                if value.casefold() in names and names[value.casefold()]!=value:
                    raise Invalid('列中存在不同大小写的同名值，不能隐式重命名其他书籍')
        backup=dict(schema='kc-summary-backup/v1' if summary else 'kc-column-backup/v1',library_uuid=library,field=field,rows=changed)
        backup['digest']=digest(backup)
        folder=Path(backup_dir);folder.mkdir(parents=True,exist_ok=True)
        path=folder/(str(uuid4())+'.json');atomic_write(path,backup)
        api.set_field(field,{r['id']:r['after'] for r in changed},allow_case_change=False)
        observed=api.all_field_for(field,ids)
        if any(not equal(observed.get(r['id']),r['after']) for r in changed):
            raise Invalid('Calibre 回读不一致；备份已保存，不能标为成功：'+str(path))
    return str(path),len(changed)


def restore(api,library,path,backup_dir):
    value=read(path); expected=value.pop('digest',None)
    if expected!=digest(value) or value.get('schema') not in ('kc-column-backup/v1','kc-summary-backup/v1') or value.get('library_uuid')!=library:
        raise Invalid('备份摘要、格式或书库不匹配')
    rows=[]
    with api.safe_read_lock:
        for original in value['rows']:
            actual=api.field_for(value['field'],original['id'])
            if api.field_for('uuid',original['id'])!=original['uuid'] or not any(equal(actual,v) for v in (original['before'],original['after'])):
                raise Invalid('备份后存在新编辑，不能覆盖恢复')
            rows.append(dict(original,before=actual,after=original['before']))
    return apply(api,library,value['field'],rows,backup_dir,summary=value['schema']=='kc-summary-backup/v1')


def receipt_plan(profile,job,result,metadata,only_ops=None):
    statuses=validate_receipt(job['request'],result)
    if result.get('book_bindings'):
        from .deferred import mapped_job
        job=mapped_job(job,result['book_bindings'])
    cat=Catalog(job['snapshot']); names={cid:c['name'] for cid,c in cat.collections.items()}
    members={cid:set(cat.members[cid]) for cid in names}; affected={}
    for op in job['request']['operations']:
        if statuses[op['op_id']]['status']!='confirmed': continue
        cid=op['collection_uuid']; kind=op['kind']; args=op['args']
        if kind=='verify_state':continue
        books=set(args.get('members',())) if kind in ('add_members','remove_members') else set(members.get(cid,()))
        if only_ops is None or op['op_id'] in only_ops:
            for bid in books: affected.setdefault(bid,set()).add(cid)
        if kind=='create_collection': names[cid]=args['name'];members[cid]=set()
        elif kind=='rename_collection': names[cid]=args['name']
        elif kind=='add_members': members[cid].update(args['members'])
        elif kind=='remove_members': members[cid].difference_update(args['members'])
        elif kind=='delete_collection': names.pop(cid,None);members.pop(cid,None)
    rows=[]
    for row in metadata['rows']:
        uid=row['uuid']; base=profile['column_baseline'].get(uid)
        if not base or uid not in job['submitted']: continue
        targets={cid for bid in base['copies'] for cid in affected.get(bid,())}
        if not targets: continue
        old_names={name for name,cid in profile['bindings'].items() if cid in targets}
        old_names.update(c['name'] for cid,c in cat.collections.items() if cid in targets)
        actual_names={names[cid] for cid in targets if cid in names and any(b in members[cid] for b in base['copies'])}
        submitted=job['submitted'][uid]; current=row['fields'][profile['field']]
        confirmed=(set(submitted)-old_names)|actual_names
        rows.append(dict(id=row['id'],uuid=uid,title=row['title'],before=sorted(current),
                         after=reconcile_column(submitted,current,confirmed),
                         baseline_remove=sorted(old_names),baseline_add=sorted(actual_names),
                         target_collections=sorted(targets),
                         confirmed_edges=[[cid,b] for cid in sorted(targets) if cid in members
                                          for b in base['copies'] if b in members[cid]]))
    return rows


def accept_backfill(profile,rows):
    """Record confirmed device relationships, never adopt unrelated later column edits."""
    from .ledger import edge_key
    p=dict(profile,column_baseline=dict(profile['column_baseline']),
           ledger=dict(profile['ledger'],claims=dict(profile['ledger']['claims'])))
    source='column:'+p['field']
    # Index once; avoid a full claims scan for every affected book.
    claims_by_book={}
    for key,claim in p['ledger']['claims'].items():
        claims_by_book.setdefault(claim['book_uuid'],[]).append(key)
    for row in rows:
        base=p['column_baseline'].get(row['uuid'])
        if not base:continue
        base=dict(base);p['column_baseline'][row['uuid']]=base
        base['values']=sorted((set(base['values'])-set(row['baseline_remove']))|set(row['baseline_add']))
        targets=set(row['target_collections']);copies=set(base['copies'])
        for key in [k for bid in copies for k in claims_by_book.get(bid,[])]:
            claim=p['ledger']['claims'].get(key)
            if claim is None:continue
            if claim['collection_uuid'] in targets and claim['book_uuid'] in copies:
                claim=dict(claim);p['ledger']['claims'][key]=claim
                claim['sources']=[s for s in claim['sources'] if s!=source]
                if not claim['sources']:del p['ledger']['claims'][key]
        for cid,bid in row['confirmed_edges']:
            key=edge_key(cid,bid)
            if cid in p['ledger']['tombstones'] or key in p['ledger']['exclusions']:continue
            claim=dict(p['ledger']['claims'].get(key,dict(collection_uuid=cid,book_uuid=bid,sources=[])))
            p['ledger']['claims'][key]=claim
            claim['sources']=sorted(set(claim['sources'])|{source})
    return p
