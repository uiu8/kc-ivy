"""Refresh the desktop view from USB files without writing native collections."""
from copy import deepcopy
from pathlib import PurePosixPath
from .protocol import Invalid, checkpoint


def refresh_files(store, snapshot):
    store.missing_books = []
    # MTP listings are cached and may intentionally omit files. Absence there
    # is not deletion evidence.
    if getattr(store, 'experimental_mtp', False):
        return snapshot
    store.check_identity()
    if not (store.mount / 'documents').is_dir():
        raise Invalid('设备 documents 目录不可访问，请重新连接；未刷新书籍状态')
    relations = {}
    for row in snapshot['relations']:
        relations.setdefault(row['book_uuid'], []).append(row)
    missing = set()
    for book in snapshot['books']:
        checkpoint()
        location = book.get('location') or ''
        path = PurePosixPath(location)
        if not location.startswith('/mnt/us/documents/') or '..' in path.parts:
            continue
        file = store.mount / location[len('/mnt/us/'):]
        if not file.resolve().is_relative_to(store.mount):
            continue
        if any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)()
               for p in (file, *file.parents) if p != store.mount and store.mount in p.parents):
            continue
        try:
            file.stat()
            continue
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise Invalid('设备文件暂时无法读取，请重新连接；未将其视为删除') from exc
        # Download removal must not discard cloud/unknown collection membership.
        if any(r['sideloaded'] != 1 or r['member_present'] != 1
               for r in relations.get(book['uuid'], [])):
            continue
        missing.add(book['uuid'])
        store.missing_books.append(book)
    store.check_identity()
    if not missing:
        return snapshot
    value = deepcopy(snapshot)
    value['books'] = [b for b in value['books'] if b['uuid'] not in missing]
    value['relations'] = [r for r in value['relations'] if r['book_uuid'] not in missing]
    # This is a file-based projection, not a newly exported native snapshot.
    # The executor still requires an exact native before-state match.
    return value


def device_file_job(job, manager):
    """Only Calibre's device upload/delete operations, never library deletion."""
    func = getattr(job, 'func', None)
    return (getattr(func, '__self__', None) is manager and
            getattr(func, '__name__', '') in ('_delete_books', '_upload_books'))
