"""Pure collection planner: explicit intent, UUID scope, immutable preview."""
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from uuid import uuid4
from .protocol import Invalid, canonical, digest, loads, seal, validate, check, ARGS, ID


def state_digest(name, members):
    return digest({'name': name, 'members': sorted(members)})


class Catalog:
    def __init__(self, snapshot):
        validate(snapshot)
        self.snapshot = snapshot
        self.books = {b['uuid']: b for b in snapshot['books']}
        self.collections = {c['uuid']: c for c in snapshot['collections']}
        self.members = defaultdict(set)
        self.book_collections = defaultdict(set)
        self.names = defaultdict(list)
        for c in snapshot['collections']:
            self.names[c['name']].append(c['uuid'])
        for r in snapshot['relations']:
            self.members[r['collection_uuid']].add(r['book_uuid'])
            self.book_collections[r['book_uuid']].add(r['collection_uuid'])

    def state(self, cid):
        c = self.collections.get(cid)
        return state_digest(c['name'], self.members[cid]) if c else None


@dataclass(frozen=True)
class Plan:
    # Byte storage ensures UI consumers cannot mutate a preview after approval.
    raw: bytes

    @property
    def data(self):
        return loads(self.raw)

    @property
    def ready(self):
        return not self.data['blockers']


def plan(snapshot, intents, scope, library_uuid, inputs=None):
    """Intent args are semantic only. No implicit deletion from absent sources.

    Each scope is explicitly adopted. Own confirmed predecessors advance the
    expected state; the eventual executor must compare it before each write.
    """
    cat = Catalog(snapshot)
    collections = deepcopy(cat.collections)
    members = deepcopy(cat.members)
    authorized = set(scope)
    ops, blockers = [], []
    protected = set(snapshot['policy']['protected_collections'])
    caps = set(snapshot['capabilities']['verified_operations'])
    last_op, renames, deleted, created = {}, {}, set(), set()
    seen_ids = set()
    def block(code, message, cid):
        item = dict(code=code, message=message, collection_uuid=cid)
        if item not in blockers:
            blockers.append(item)
    for intent in intents:
        if set(intent) != {'kind', 'collection_uuid', 'args', 'reason', 'depends_on', 'intent_id'}:
            raise Invalid('意图缺失字段或包含任意命令')
        kind, cid = intent['kind'], intent['collection_uuid']
        if kind not in ARGS:
            raise Invalid('不支持的语义操作')
        check(ID, cid)
        check(ARGS[kind], intent['args'])
        iid = intent['intent_id']
        check(ID, iid)
        if iid in seen_ids or not set(intent['depends_on']).issubset(seen_ids):
            raise Invalid('意图 ID/依赖无效')
        seen_ids.add(iid)
        args = deepcopy(intent['args'])
        c = collections.get(cid)
        if kind == 'create_collection':
            if cid in collections or cid in cat.books or cid in deleted:
                block('IDENTITY_CONFLICT', '新建 UUID 已存在或本次已删除', cid)
                continue
            if any(v['name'].casefold() == args['name'].casefold() for v in collections.values()):
                block('NAME_CONFLICT', '同名或大小写相同的架需要明确选择身份', cid)
            created.add(cid)
            before = None
            collections[cid] = dict(uuid=cid, name=args['name'], complete=True)
            members[cid] = set()
        else:
            if cid not in authorized and cid not in created:
                block('OUT_OF_SCOPE', '收藏夹尚未纳入本次明确管理范围', cid)
            if not c:
                block('MISSING_COLLECTION', '收藏夹已不存在，不能按名称猜测', cid)
                continue
            if cid in protected and kind != 'verify_state':
                block('PROTECTED', '此架受 Kindle 保护策略保护', cid)
            if not c['complete']:
                # First adapter does not yet certify independent rename of unknown graphs.
                block('INCOMPLETE_MEMBERS', '包含未知关系，不能生成可发送的编辑操作', cid)
                continue
            before = state_digest(c['name'], members[cid])
            if kind in ('add_members', 'remove_members'):
                requested = set(args['members'])
                if not requested.issubset(cat.books):
                    block('UNKNOWN_BOOK', '不能对未识别的书籍 UUID 修改归属', cid)
                    continue
                actual = requested - members[cid] if kind == 'add_members' else requested & members[cid]
                args['members'] = sorted(actual)
                if not actual:
                    kind, args = 'verify_state', {}
                elif kind == 'add_members':
                    members[cid].update(actual)
                else:
                    members[cid].difference_update(actual)
            elif kind == 'rename_collection':
                if cid in renames and renames[cid] != args['name']:
                    block('RENAME_CONFLICT', '同一架存在两个改名目标', cid)
                if any(k != cid and v['name'].casefold() == args['name'].casefold()
                       for k, v in collections.items()):
                    block('NAME_CONFLICT', '目标名称已被另一收藏夹使用', cid)
                renames[cid] = args['name']
                c['name'] = args['name']
            elif kind == 'delete_collection':
                if cid in last_op:
                    block('DELETE_EDIT_CONFLICT', '同一计划不能同时编辑和删除一个架', cid)
                deleted.add(cid)
                del collections[cid]
        if kind not in caps:
            block('CAPABILITY_UNVERIFIED', '当前固件尚未通过 ' + kind + ' 真机验证', cid)
        if kind in ('add_members', 'remove_members', 'create_collection'):
            if len(members[cid]) > snapshot['policy']['max_members']:
                block('MEMBER_LIMIT', '最终成员超过保护上限', cid)
        after = (state_digest(collections[cid]['name'], members[cid]) if cid in collections else None)
        dependencies = list(intent['depends_on'])
        if cid in last_op and last_op[cid] not in dependencies:
            dependencies.append(last_op[cid])
        ops.append(dict(op_id=iid, kind=kind, collection_uuid=cid, before=before,
                        after=after, args=args, depends_on=dependencies, reason=intent['reason']))
        last_op[cid] = iid
    # An invalid predecessor cannot be silently elided from a dependency chain.
    available = {o['op_id'] for o in ops}
    for op in ops:
        if not set(op['depends_on']).issubset(available):
            block('BLOCKED_DEPENDENCY', '前置操作未能生成', op['collection_uuid'])
            op['depends_on'] = [v for v in op['depends_on'] if v in available]
    if len(created) > snapshot['policy']['max_creates']:
        block('CREATE_LIMIT', '新建数量超过单轮保护上限', None)
    if sum(o['kind'] in ('rename_collection', 'delete_collection') for o in ops) > snapshot['policy']['max_rename_delete']:
        block('EDIT_LIMIT', '改名和删除超过单轮研发保护上限', None)
    value = seal(dict(schema='kc-edit-plan/v1', plan_id=str(uuid4()),
        device=deepcopy(snapshot['device']), library_uuid=library_uuid,
        snapshot_digest=digest(snapshot), inputs_digest=digest(inputs if inputs is not None else intents),
        policy_version=snapshot['policy']['version'], capabilities_version=snapshot['capabilities']['version'],
        scope=sorted(authorized), operations=ops, blockers=blockers), 'plan_digest')
    validate(value)
    return Plan(canonical(value))


def intent(kind, cid, args=None, reason='手动编辑', depends_on=()):
    return dict(kind=kind, collection_uuid=cid, args=args or {}, reason=reason,
                depends_on=list(depends_on), intent_id=str(uuid4()))


def move(book_ids, source, target):
    if source == target:
        raise Invalid('移动的源和目标不能相同')
    add = intent('add_members', target, dict(members=sorted(set(book_ids))), '移动：先加入目标')
    remove = intent('remove_members', source, dict(members=sorted(set(book_ids))),
                    '移动：目标确认后退出源', [add['intent_id']])
    return [add, remove]


def request(plan_value, current_snapshot, current_inputs_digest):
    p = validate(plan_value.data)
    if p['blockers']:
        raise Invalid('预览有阻断项，不能发送')
    if p['snapshot_digest'] != digest(current_snapshot) or p['inputs_digest'] != current_inputs_digest:
        raise Invalid('预览已过期，请重新预览')
    if not p['operations']:
        raise Invalid('没有待执行或核验的操作')
    value = seal(dict(schema='kc-edit-request/v1', job_id=str(uuid4()), plan_id=p['plan_id'],
        plan_digest=p['plan_digest'], device=p['device'], library_uuid=p['library_uuid'],
        snapshot_digest=p['snapshot_digest'], policy_version=p['policy_version'],
        capabilities_version=p['capabilities_version'], operations=p['operations']), 'request_digest')
    return validate(value)
