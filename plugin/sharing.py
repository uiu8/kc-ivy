"""Streamed book packages; never opens a sender's Calibre database."""
import hashlib
import os
import tempfile
import shutil
from pathlib import Path,PurePosixPath
from zipfile import ZipFile,ZIP_STORED
from uuid import uuid4
from .protocol import Invalid,canonical,loads,digest,checkpoint
from .migration import SCHEMA,seal_backup,validate_backup,now

FORMATS={'EPUB','AZW3','AZW','MOBI','PDF','TXT','DOCX','RTF','FB2','CBZ','CBR','KFX'}


def ensure_import_space(library_path,files):
    sizes=[f['size'] for f in files]
    if not sizes:return
    destination=Path(library_path);temporary=Path(tempfile.gettempdir())
    required=sum(sizes);scratch=max(sizes);reserve=16*1024*1024
    if destination.stat().st_dev==temporary.stat().st_dev:
        needs=[(destination,required+scratch)]
    else:needs=[(destination,required),(temporary,scratch)]
    for path,size in needs:
        free=shutil.disk_usage(path).free
        if free<size+reserve:
            raise Invalid(f'导入空间不足：{path}；估算需 {(size+reserve)/1024**2:.1f} MiB，可用 {free/1024**2:.1f} MiB。释放空间后可从原导入记录继续。')

def share_inventory(api,ids,formats):
    formats=set(formats)&FORMATS;rows=[]
    for ident in ids:
        checkpoint();available=set(api.formats(ident) or ());selected=sorted(available & formats)
        rows.append(dict(id=ident,uuid=api.field_for('uuid',ident),title=api.field_for('title',ident),formats=selected,
                         status='包含电子书文件' if selected else '仅元数据记录：将跳过' if not available else '所选格式不存在：将跳过'))
    return dict(rows=rows,fingerprint=digest(rows))

def export_share(api,library,ids,field,path,formats,selected_names=None,expected_inventory=None):
    from .column_io import assert_field
    assert_field(api,field);formats=set(formats)&FORMATS
    if not formats:raise Invalid('请选择至少一种导出格式')
    if expected_inventory and share_inventory(api,ids,formats)['fingerprint']!=expected_inventory:raise Invalid('书库格式或记录已变化，请重新预览导出清单。')
    path=Path(path);partial=path.with_name(path.name+'.partial')
    if path.exists() or partial.exists():raise Invalid('输出文件已存在，请选择新文件名')
    books=[];collections={};relations=[];files=[];skipped=0
    try:
        with ZipFile(partial,'x',compression=ZIP_STORED,allowZip64=True) as archive:
            for book_id in ids:
                checkpoint();mi=api.get_metadata(book_id,get_cover=False,get_user_categories=False)
                selected=sorted(set(api.formats(book_id) or ())&formats)
                if not selected:skipped+=1;continue
                bid=str(uuid4());books.append(dict(id=bid,title=mi.title or '',authors=list(mi.authors or []),calibre_uuid=mi.uuid))
                names=set(api.field_for(field,book_id) or ())
                if selected_names is not None:
                    wanted=set(selected_names.get(book_id,()))
                    if not wanted.issubset(names):raise Invalid('书架列已变化，请重新读取分享书架后选择')
                    names=wanted
                for name in sorted(names):
                    if name not in collections:collections[name]=str(uuid4())
                    relations.append([collections[name],bid])
                for fmt in selected:
                    filename='books/'+bid+'/'+fmt.lower()+'.'+fmt.lower();hasher=hashlib.sha256();size=0
                    # Temporary format handle avoids loading a book into RAM.
                    source=api.format(book_id,fmt,as_file=True)
                    if source is None:raise Invalid('书籍格式已变化，请重新导出')
                    with source,archive.open(filename,'w',force_zip64=True) as target:
                        while True:
                            checkpoint();chunk=source.read(1024*1024)
                            if not chunk:break
                            target.write(chunk);hasher.update(chunk);size+=len(chunk)
                    files.append(dict(book=bid,format=fmt,path=filename,size=size,sha256=hasher.hexdigest()))
            if not books:raise Invalid('所选书籍没有选中的可导出格式')
            backup=seal_backup(dict(schema=SCHEMA,backup_id=str(uuid4()),created_utc=now(),snapshot_utc=None,
                source=dict(library_uuid=library,kind='calibre-column',field=field),
                books=books,collections=[dict(id=v,name=k) for k,v in collections.items()],relations=relations,unresolved_relations=[]))
            manifest=dict(schema='kc-book-share/v1',backup=backup,files=files)
            manifest['digest']=digest(manifest);archive.writestr('manifest.json',canonical(manifest))
        os.rename(partial,path)
        return dict(books=len(books),skipped=skipped,collections=len(collections),size=path.stat().st_size,path=str(path))
    except BaseException:
        if partial.exists():partial.unlink()
        raise

def inspect_share(path):
    with ZipFile(path) as archive:
        names=archive.namelist()
        if len(set(names))!=len(names):raise Invalid('分享包有重复路径')
        info=archive.getinfo('manifest.json')
        if info.file_size>64*1024*1024:raise Invalid('分享清单超过64MiB')
        manifest=loads(archive.read(info))
        if manifest.get('schema')!='kc-book-share/v1' or manifest.get('digest')!=digest({k:v for k,v in manifest.items() if k!='digest'}):raise Invalid('分享清单格式或摘要无效')
        backup=validate_backup(manifest['backup']);bids={b['id'] for b in backup['books']};seen=set();bookfiles=set()
        for f in manifest['files']:
            p=PurePosixPath(f['path']);fmt=f['format']
            if p.is_absolute() or '..' in p.parts or '\\' in f['path'] or not f['path'].startswith('books/') or fmt not in FORMATS or p.suffix.lower()!='.'+fmt.lower():raise Invalid('分享文件路径或格式不允许')
            if f['book'] not in bids or f['path'] in seen or (f['book'],fmt) in bookfiles:raise Invalid('分享文件引用无效或重复')
            member=archive.getinfo(f['path'])
            if member.file_size!=f['size'] or (member.external_attr>>16)&0o170000==0o120000:raise Invalid('文件大小不符或是链接')
            if not isinstance(f['sha256'],str) or len(f['sha256'])!=64:raise Invalid('文件摘要无效')
            seen.add(f['path']);bookfiles.add((f['book'],fmt))
        if set(names)!=seen|{'manifest.json'} or {b for b,_ in bookfiles}!=bids:raise Invalid('分享包有未声明文件或缺失书籍格式')
        return manifest

def verify_files(path,manifest):
    """Verify before any Calibre writes; second read during import is deliberate."""
    with ZipFile(path) as archive:
        for f in manifest['files']:
            checkpoint();h=hashlib.sha256()
            with archive.open(f['path']) as stream:
                while True:
                    checkpoint();chunk=stream.read(1024*1024)
                    if not chunk:break
                    h.update(chunk)
            if h.hexdigest()!=f['sha256']:raise Invalid('书籍文件校验失败：'+f['path'])

def import_share(api,library,path,field,state,reuse=None,expected=None):
    from calibre.ebooks.metadata.book.base import Metadata
    from .column_io import assert_field
    assert_field(api,field);manifest=inspect_share(path)
    if expected and manifest['digest']!=expected:raise Invalid('预览后分享包改变，请重新读取')
    verify_files(path,manifest)
    backup=manifest['backup'];reuse=reuse or {};key=digest(['share-import',library,manifest['digest']])
    def check_names():
        existing={v.casefold():v for v in api.all_field_names(field)}
        for collection in backup['collections']:
            name=collection['name'];known=existing.setdefault(name.casefold(),name)
            if known!=name:
                raise Invalid(f'分享分类“{name}”与接收列中的“{known}”大小写冲突；请统一名称或选择独立书库，未重命名已有分类')
            if ',' in name or '\n' in name or name!=name.strip():raise Invalid('分享分类名称不能无损写入多值列，请先整理来源名称')
    # Check before creating any books, and again under the write lock below.
    with api.safe_read_lock:check_names()
    journal=state.workspace(key)
    if journal and journal['field']!=field:raise Invalid('本包已使用另一列导入，请先使用原列完成')
    if journal is None:
        journal=dict(id=key,kind='share_import',profile=library,revision=0,field=field,manifest=manifest,
            path=str(path),items={},created=now(),complete=False)
        journal=state.save_workspace(journal,0)
    remaining=[f for f in manifest['files'] if not journal['items'].get(f['book'],{}).get('complete')
        and not journal['items'].get(f['book'],{}).get('reuse') and f['book'] not in reuse
        and f['format'] not in journal['items'].get(f['book'],{}).get('formats',[])]
    ensure_import_space(api.backend.library_path,remaining)
    ids=set(api.all_book_ids());uuids=api.all_field_for('uuid',ids);byuuid={}
    for i,uid in uuids.items():byuuid.setdefault(uid,[]).append(i)
    names={c['id']:c['name'] for c in backup['collections']};values={b['id']:[] for b in backup['books']};files={b:[] for b in values}
    for cid,bid in backup['relations']:values[bid].append(names[cid])
    for f in manifest['files']:files[f['book']].append(f)
    changed=[]
    with ZipFile(path) as archive,tempfile.TemporaryDirectory(prefix='kcshare-') as tmp:
        for b in backup['books']:
            checkpoint();bid=b['id'];item=journal['items'].get(bid)
            if item is None:
                local=reuse.get(bid)
                if local is not None and local not in ids:raise Invalid('选中的已有书籍不存在')
                item=dict(uuid=uuids[local] if local is not None else str(uuid4()),reuse=local is not None,formats=[],column_done=False)
                journal['items'][bid]=item;journal=state.save_workspace(journal,journal['revision']);item=journal['items'][bid]
            candidates=byuuid.get(item['uuid'],[])
            if len(candidates)>1:raise Invalid('本地书籍 UUID 重复，无法安全继续导入')
            local=candidates[0] if candidates else None
            if item.get('complete'):
                if local is None:raise Invalid('之前导入的书籍已删除，请检查原导入记录')
                continue
            if local is None:
                if item['reuse']:raise Invalid('原来复用的书籍已经不存在')
                mi=Metadata(b['title'],b.get('authors') or ['未知']);mi.uuid=item['uuid']
                local=api.create_book_entry(mi,add_duplicates=True,preserve_uuid=True,apply_import_tags=False)
                byuuid[item['uuid']]=[local]
            if not item['reuse']:
                for f in files[bid]:
                    if f['format'] in item['formats'] and f['format'] in (api.formats(local) or ()):continue
                    if f['format'] in (api.formats(local) or ()):
                        existing=hashlib.sha256()
                        with api.format(local,f['format'],as_file=True) as stream:
                            while True:
                                checkpoint();chunk=stream.read(1024*1024)
                                if not chunk:break
                                existing.update(chunk)
                        if existing.hexdigest()!=f['sha256']:raise Invalid('中断后书籍格式被修改，停止覆盖：'+b['title'])
                        item['formats'].append(f['format']);journal=state.save_workspace(journal,journal['revision']);item=journal['items'][bid]
                        continue
                    checkpoint();temp=Path(tmp)/('format.'+f['format'].lower());h=hashlib.sha256()
                    with archive.open(f['path']) as source,temp.open('wb') as target:
                        while True:
                            checkpoint();chunk=source.read(1024*1024)
                            if not chunk:break
                            target.write(chunk);h.update(chunk)
                    if h.hexdigest()!=f['sha256']:raise Invalid('导入期间文件发生变化')
                    if not api.add_format(local,f['format'],str(temp),replace=True,run_hooks=False):raise Invalid('Calibre 未能加入书籍格式')
                    item['formats']=sorted(set(item['formats'])|{f['format']});journal=state.save_workspace(journal,journal['revision']);item=journal['items'][bid]
            if not item['column_done']:
                with api.write_lock:
                    check_names()
                    before=sorted(api.field_for(field,local) or ());after=sorted(set(before)|set(values[bid]))
                    if 'column_before' not in item:
                        item.update(column_before=before,column_after=after)
                        journal=state.save_workspace(journal,journal['revision']);item=journal['items'][bid]
                    elif before not in (item['column_before'],item['column_after']):raise Invalid('中断后列值被修改，请检查导入记录后继续')
                    # Calibre shares category names across books: never rename them globally.
                    api.set_field(field,{local:item['column_after']},allow_case_change=False)
                    if sorted(api.field_for(field,local) or ())!=item['column_after']:raise Invalid('列回读不一致')
                    item['column_done']=True
            item['complete']=True;journal=state.save_workspace(journal,journal['revision']);changed.append(local)
    journal['complete']=True;journal=state.save_workspace(journal,journal['revision'])
    return journal,changed

def local_backup(journal):
    if not journal['complete']:raise Invalid('请先完成书籍导入')
    value={k:v for k,v in journal['manifest']['backup'].items() if k!='digest'}
    value=dict(value,source=dict(library_uuid=journal['profile'],kind='imported-share'),
        books=[dict(b,calibre_uuid=journal['items'][b['id']]['uuid']) for b in value['books']])
    return seal_backup(value)

def new_library(path):
    from calibre.db.legacy import LibraryDatabase
    path=Path(path)
    if path.exists() and any(path.iterdir()):raise Invalid('独立书库请选择空目录，已有书库不会覆盖')
    path.mkdir(parents=True,exist_ok=True)
    db=LibraryDatabase(str(path))
    try:db.create_custom_column('kindlecollections','Kindle书架','text',is_multiple=True)
    finally:db.close()
    return LibraryDatabase(str(path))
