"""Expand source scope using stable metadata IDs; never infer device removals."""
from .metadata import mappings,assert_library


def available_books(snapshot,metadata):
    allowed={b['uuid'] for b in snapshot['books']
             if not (b.get('location') or b.get('title') or '').strip().casefold().endswith('.sh')}
    return {uid:kept for uid,ids in mappings(snapshot,metadata['rows']).items()
            if (kept:=[bid for bid in ids if bid in allowed])}


def expand(snapshot,metadata,profile):
    assert_library(snapshot,profile['library_uuid'],metadata)
    available=available_books(snapshot,metadata)
    bases=dict(profile['column_baseline']);count=0
    for row in metadata['rows']:
        uid=row['uuid'];ids=available.get(uid,[])
        old=bases.get(uid)
        if old:
            kept=[bid for bid in old['copies'] if not bid.startswith('kc-new-') or bid in ids]
            if kept!=old['copies']:
                old=dict(old,copies=kept,pending_copies={bid:n for bid,n in old.get('pending_copies',{}).items() if bid in kept})
                bases[uid]=old;count+=1
        new=set(ids)-set(old['copies'] if old else ())
        if not new:continue
        count+=len(new)
        if old:
            # Existing copies keep their baseline, including unsent column edits.
            base=dict(old,copies=sorted(set(old['copies'])|new))
            pending={bid:list(names) for bid,names in old.get('pending_copies',{}).items()}
            names=sorted(set(old['values']) & set(row['fields'][profile['field']]))
            if names:
                for bid in new:pending[bid]=names
            base['pending_copies']=pending
        else:
            # Empty baseline requests only additions from current column values.
            # An empty column never claims or removes preexisting Kindle edges.
            base=dict(id=row['id'],copies=sorted(new),values=[])
        bases[uid]=base
    return dict(profile,column_baseline=bases),count
