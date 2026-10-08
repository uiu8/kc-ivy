"""USB/MTP new-book discovery and deferred identity binding.

Pending IDs are local preview identities, never Kindle Entry UUIDs.
"""
import hashlib
from collections import defaultdict
from copy import deepcopy
from pathlib import PurePosixPath
from .protocol import Invalid,digest,seal,checkpoint,loads,progress

PREFIX='kc-new-'
FORMATS={'.azw','.azw3','.mobi','.pdf','.txt','.prc','.kfx'}

def pending(uid):return isinstance(uid,str) and uid.startswith(PREFIX)

def file_digest(path):
    if hasattr(path,'ebook_digest'):return path.ebook_digest()
    h=hashlib.sha256()
    with path.open('rb') as stream:
        while True:
            checkpoint();data=stream.read(1024*1024)
            if not data:break
            h.update(data)
    return h.hexdigest()

def discover(store,snapshot):
    from .transport import read
    if getattr(store,'experimental_mtp',False):
        marker=store.calibre_path('metadata');drive=store.calibre_path('driveinfo')
    else:
        marker=store.mount/'metadata.calibre';drive=store.mount/'driveinfo.calibre'
    device=read(drive) if drive.is_file() else {};reported_library=device.get('last_library_uuid')
    snapshot_library=snapshot['mapping']['calibre_library_uuid']
    foreign=bool(reported_library and snapshot_library and reported_library!=snapshot_library)
    # Calibre can persist null here while connected. Retain the last known
    # library context; actual column mapping still requires matching book UUIDs.
    library=snapshot_library if foreign else reported_library or snapshot_library
    namespace=snapshot['device']['instance_id']
    raw=marker.read_bytes() if marker.is_file() else None
    metadata=loads(raw) if raw is not None else []
    if not isinstance(metadata,list) or foreign:metadata=[]
    known={b['location'] for b in snapshot['books']};candidates=defaultdict(list)
    for b in metadata:
        if not isinstance(b,dict) or not b.get('uuid') or not isinstance(b.get('lpath'),str):continue
        rel=PurePosixPath(b['lpath'].replace('\\','/'))
        if rel.is_absolute() or '..' in rel.parts or not rel.parts or rel.parts[0]!='documents' or rel.suffix.lower() not in FORMATS:continue
        if any(p.startswith('.') or p.lower().endswith('.sdr') for p in rel.parts[1:-1]):continue
        location='/mnt/us/'+str(rel)
        if location not in known:candidates[location].append(b)
    # Walk only document folders, never companion assets or linked directories.
    folders=[store.mount/'documents']
    progress('正在扫描 documents 文件目录…')
    while folders:
        folder=folders.pop()
        if not folder.is_dir() or folder.is_symlink():continue
        for path in folder.iterdir():
            checkpoint()
            if path.is_symlink():continue
            if path.is_dir():
                if not path.name.startswith('.') and not path.name.lower().endswith('.sdr'):folders.append(path)
                continue
            rel=PurePosixPath(str(path.relative_to(store.mount)).replace('\\','/'))
            location='/mnt/us/'+str(rel)
            if rel.suffix.lower() in FORMATS and location not in known:candidates.setdefault(location,[])
    extra=[];descriptors=[]
    for number,(location,rows) in enumerate(sorted(candidates.items()),1):
        checkpoint();progress(f'正在核验候选文件 {number}/{len(candidates)}：'+location.rsplit('/',1)[-1])
        b=rows[0] if len(rows)==1 else {};path=store.mount/location[len('/mnt/us/'):]
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(store.mount):continue
        before=path.stat();sha=store.discovery_digest(path,raw or b'') if hasattr(store,'discovery_digest') else file_digest(path);after=path.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise Invalid('传书尚未结束，请等待完成后重新读取')
        uid=PREFIX+digest([namespace,location,sha])
        descriptors.append(dict(alias=uid,location=location,size=after.st_size,sha256=sha))
        extra.append(dict(uuid=uid,title=b.get('title') or path.stem,location=location,cde_key=None,cde_type=None,calibre_uuid=b.get('uuid'),metadata_matches=1 if b else 0,collection_count=0))
    if (marker.read_bytes() if marker.is_file() else None)!=raw:raise Invalid('设备书单正在更新，请稍后重新读取')
    if not extra:return snapshot,[]
    value=deepcopy(snapshot);value['books'].extend(extra)
    value['mapping']['calibre_library_uuid']=library
    return value,descriptors

def previous_aliases(snapshot,book,descriptor,library=None):
    """Recognize old PC-only aliases without weakening the file fingerprint."""
    ns=snapshot['device']['instance_id'];path=descriptor['location'];sha=descriptor['sha256']
    return {PREFIX+digest([ns,path,sha])}|{PREFIX+digest([scope,uid,path,sha])
        for scope in (ns,library,snapshot['mapping'].get('calibre_library_uuid')) if scope
        for uid in (None,book.get('calibre_uuid'))}

def draft_book_bindings(old,new,store):
    bypath=defaultdict(list)
    for book in new['books']:bypath[book.get('location')].append(book)
    descriptors={d['location']:d for d in getattr(store,'new_books',[])};result={}
    for book in old['books']:
        if not pending(book['uuid']):continue
        matches=bypath.get(book.get('location'),[])
        if len(matches)!=1:continue
        target=matches[0]
        if target['uuid']==book['uuid']:continue
        d=descriptors.get(book['location'])
        if d is None:
            location=book.get('location') or ''
            if not location.startswith('/mnt/us/documents/'):continue
            path=store.mount/location[len('/mnt/us/'):]
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(store.mount):continue
            d=dict(location=location,sha256=file_digest(path))
        if book['uuid'] in previous_aliases(old,book,d):result[book['uuid']]=target['uuid']
    return result

def extend_request(request,descriptors):
    wanted={u for op in request['operations'] for u in op['args'].get('members',[]) if pending(u)}
    if not wanted:return request
    books=[b for b in descriptors if b['alias'] in wanted]
    if {b['alias'] for b in books}!=wanted:raise Invalid('待识别书籍文件已变化，请重新读取并预览')
    value={k:v for k,v in request.items() if k!='request_digest'}
    value.update(schema='kc-edit-request/v2',new_books=books)
    return seal(value,'request_digest')

def remap_profile(profile,bindings):
    from .ledger import edge_key
    mapping={b['alias']:b['uuid'] for b in bindings}
    if not mapping:return profile
    p=deepcopy(profile)
    for base in p['column_baseline'].values():
        base['copies']=sorted({mapping.get(uid,uid) for uid in base['copies']})
        if 'pending_copies' in base:base['pending_copies']={mapping.get(uid,uid):v for uid,v in base['pending_copies'].items()}
    ledger=p['ledger'];old_keys={};claims={}
    for key,row in ledger['claims'].items():
        row['book_uuid']=mapping.get(row['book_uuid'],row['book_uuid']);new=edge_key(row['collection_uuid'],row['book_uuid']);old_keys[key]=new;claims[new]=row
    for cid in set(p['bindings'].values())|set(ledger['tombstones']):
        for alias,uid in mapping.items():old_keys[edge_key(cid,alias)]=edge_key(cid,uid)
    ledger['claims']=claims;ledger['exclusions']=[old_keys.get(k,k) for k in ledger['exclusions']]
    return p


def mapped_job(job,bindings):
    mapping={b['alias']:b['uuid'] for b in bindings};job=deepcopy(job)
    for b in job['snapshot']['books']:b['uuid']=mapping.get(b['uuid'],b['uuid'])
    for r in job['snapshot']['relations']:r['book_uuid']=mapping.get(r['book_uuid'],r['book_uuid'])
    for op in job['request']['operations']:
        if 'members' in op['args']:op['args']['members']=[mapping.get(u,u) for u in op['args']['members']]
    return job


def recovery_bindings(request,snapshot,store,operations):
    bypath=defaultdict(list)
    for b in snapshot['books']:bypath[b['location']].append(b['uuid'])
    bindings=[]
    wanted={u for o in operations for u in o['args'].get('members',[])}
    for b in request.get('new_books',[]):
        if b['alias'] not in wanted:continue
        ids=bypath[b['location']]
        path=store.mount/b['location'][len('/mnt/us/'):]
        if len(ids)!=1 or not path.is_file() or file_digest(path)!=b['sha256']:
            raise Invalid('原任务的新书已移动、变化或未识别，请重新选择书籍整理')
        bindings.append(dict(alias=b['alias'],uuid=ids[0]))
    return bindings
