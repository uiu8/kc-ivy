"""Accepted source claims, explicit exclusions and verified-result reconciliation.

No Calibre writes here. Adopting a baseline is an explicit setup action, not a
side effect of finding the same name on a Kindle. Missing records aren't clears.
"""
from copy import deepcopy
from collections import defaultdict
from .protocol import Invalid, digest, validate
from .planner import intent


def edge_key(cid, bid):
    # Do not use a name or an ambiguous concatenated key as relationship identity.
    return digest([cid, bid])


def empty_ledger(device, library_uuid):
    return dict(version=1, device=deepcopy(device), library_uuid=library_uuid,
                claims={}, exclusions=[], receipts={}, tombstones=[])


def adopt(ledger, source, edges):
    out = deepcopy(ledger)
    for cid, bid in edges:
        key = edge_key(cid, bid)
        row = out['claims'].setdefault(key, dict(collection_uuid=cid, book_uuid=bid, sources=[]))
        row['sources'] = sorted(set(row['sources']) | {source})
    return out


def source_delta(ledger, source, desired, accepted_books):
    """Missing/deleted Calibre records are excluded by accepted_books, not cleared.

    Returns intents and a proposal ledger. The caller must not persist the
    proposal until the corresponding operations (including verify_state) confirm.
    """
    desired = set(map(tuple, desired))
    allowed = set(accepted_books)
    if any(bid not in allowed for _, bid in desired):
        raise Invalid('来源包含未采用的书籍副本')
    old = {(r['collection_uuid'], r['book_uuid']) for r in ledger['claims'].values()
           if source in r['sources'] and r['book_uuid'] in allowed}
    proposal = deepcopy(ledger)
    ops, conflicts = [], []
    additions, removals, verifies = defaultdict(set), defaultdict(set), set()
    for cid, bid in sorted(desired - old):
        key = edge_key(cid, bid)
        if key in proposal['exclusions'] or cid in proposal['tombstones']:
            conflicts.append([cid, bid, '手动排除或已删除身份，不能由来源自动复活'])
            continue
        row = proposal['claims'].setdefault(key, dict(collection_uuid=cid, book_uuid=bid, sources=[]))
        row['sources'] = sorted(set(row['sources']) | {source})
        additions[cid].add(bid)
    for cid, bid in sorted(old - desired):
        key = edge_key(cid, bid)
        row = proposal['claims'][key]
        row['sources'].remove(source)
        if row['sources']:
            verifies.add(cid)
        else:
            del proposal['claims'][key]
            removals[cid].add(bid)
    for cid, bids in sorted(additions.items()):
        ops.append(intent('add_members', cid, dict(members=sorted(bids)), '来源 ' + source + ' 新增声明'))
    for cid, bids in sorted(removals.items()):
        ops.append(intent('remove_members', cid, dict(members=sorted(bids)), '最后一个已采用来源撤回'))
    for cid in sorted(verifies - additions.keys() - removals.keys()):
        ops.append(intent('verify_state', cid, reason='撤回一个来源，其他来源仍保留关系'))
    return dict(intents=ops, proposal=proposal, conflicts=conflicts)


def validate_receipt(request, result):
    validate(request)
    validate(result)
    if (request['job_id'] != result['job_id'] or request['request_digest'] != result['request_digest']
            or request['device'] != result['device']):
        raise Invalid('回执不属于此设备或任务')
    if request['schema'].rsplit('/',1)[1]!=result['schema'].rsplit('/',1)[1]:raise Invalid('任务与回执协议版本不匹配')
    if request['schema']=='kc-edit-request/v2':
        bindings=result.get('book_bindings',[]);aliases={b['alias'] for b in bindings};known={b['alias'] for b in request['new_books']}
        if result['schema']!='kc-edit-result/v2' or len(aliases)!=len(bindings) or len({b['uuid'] for b in bindings})!=len(bindings) or not aliases.issubset(known) or any(b['uuid'].startswith('kc-new-') for b in bindings):raise Invalid('新书回执的身份映射无效')
        confirmed={r['op_id'] for r in result['operations'] if r['status']=='confirmed'}
        for op in request['operations']:
            if op['op_id'] in confirmed and not (set(op['args'].get('members',[])) & known).issubset(aliases):raise Invalid('确认的新书操作缺少设备身份映射')
    requested = {o['op_id']: o for o in request['operations']}
    observed = {o['op_id']: o for o in result['operations']}
    if set(requested) != set(observed):
        raise Invalid('回执必须列出全部操作，未执行项也不能省略')
    for oid, row in observed.items():
        if row['status'] == 'confirmed':
            if row['observed_digest'] != requested[oid]['after']:
                raise Invalid('确认结果与计划的真实目标状态不一致')
            if any(observed[d]['status'] != 'confirmed' for d in requested[oid]['depends_on']):
                raise Invalid('依赖未确认，不能确认后续操作')
    return observed


def reconcile_column(submitted, current, confirmed):
    """Three-way merge. Preserve all user edits made since submission.

    Caller supplies only values covered by confirmed device operations. Returns
    a proposed value; persistence still needs Calibre write lock and backup.
    """
    submitted, current, confirmed = map(set, (submitted, current, confirmed))
    return sorted((confirmed | (current - submitted)) - (submitted - current))
