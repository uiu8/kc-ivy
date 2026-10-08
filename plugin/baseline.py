"""Recognize only unambiguous column/device agreement; preserve real edits."""
from .planner import Catalog
from .metadata import mappings,assert_library
from .ledger import edge_key

def align(snapshot,metadata,profile,legacy_names=False):
    assert_library(snapshot,profile['library_uuid'],metadata)
    p=dict(profile,bindings=dict(profile['bindings']),column_baseline=dict(profile['column_baseline']),
           ledger=dict(profile['ledger'],claims=dict(profile['ledger']['claims'])))
    cat=Catalog(snapshot);mapped=mappings(snapshot,metadata['rows'])
    source='column:'+p['field'];changes=[]
    excluded=set(p['ledger']['exclusions']);deleted=set(p['ledger']['tombstones'])
    aliases={}
    for c in snapshot['collections']:
        aliases.setdefault(c['name'].replace(',',';'),[]).append(c['uuid'])
    counts={}
    for r in snapshot['relations']:
        edge=(r['collection_uuid'],r['book_uuid']);counts[edge]=counts.get(edge,0)+1
    for row in metadata['rows']:
        uid=row['uuid'];base=p['column_baseline'].get(uid)
        if not base:continue
        base=dict(base,values=list(base['values']));p['column_baseline'][uid]=base
        copies=base['copies']
        if not copies or not set(copies).issubset(mapped.get(uid,())):continue
        for name in set(row['fields'][p['field']]):
            ids=([p['bindings'][name]] if name in p['bindings'] else cat.names.get(name,[]))
            if not ids and legacy_names:ids=aliases.get(name,[])
            if len(ids)!=1:continue
            cid=ids[0]
            if cid in deleted or cid not in cat.collections:continue
            if name in p['bindings'] and p['bindings'][name]!=cid:continue
            keys=[edge_key(cid,bid) for bid in copies]
            if any(key in excluded for key in keys) or not all(counts.get((cid,bid))==1 for bid in copies):continue
            changed=name not in base['values']
            for key,bid in zip(keys,copies):
                old=p['ledger']['claims'].get(key,dict(collection_uuid=cid,book_uuid=bid,sources=[]))
                claim=dict(old,sources=list(old['sources']));p['ledger']['claims'][key]=claim
                if source not in claim['sources']:claim['sources'].append(source);claim['sources'].sort();changed=True
            base['values']=sorted(set(base['values'])|{name});p['bindings'][name]=cid
            if changed:changes.append(dict(title=row['title'],collection=name,device_name=cat.collections[cid]['name'],collection_uuid=cid))
    return p,changes
