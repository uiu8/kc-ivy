"""KC wire contracts. No Calibre, GUI, filesystem or third-party dependencies.

The published JSON Schemas and runtime validator use the same definitions.
Digests detect changes, not authenticity. Never dispatch arbitrary wire commands.
"""
import hashlib
import json
import re
import time
import threading
from contextlib import contextmanager
from copy import deepcopy

MAX_JSON = 64 * 1024 * 1024
KINDS = ('create_collection', 'add_members', 'remove_members',
         'rename_collection', 'delete_collection', 'verify_state')


class Invalid(ValueError):
    pass


class Cancelled(RuntimeError):
    pass


_work = threading.local()


@contextmanager
def cancellable(event):
    previous = getattr(_work, 'cancel', None)
    _work.cancel = event
    try: yield
    finally: _work.cancel = previous


def checkpoint():
    event = getattr(_work, 'cancel', None)
    if event is not None and event.is_set():
        raise Cancelled('已取消计算，设备与 Calibre 未修改。')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')

@contextmanager
def reporting(callback):
    previous=getattr(_work,'progress',None);_work.progress=callback
    try:yield
    finally:_work.progress=previous

def progress(message):
    callback=getattr(_work,'progress',None)
    if callback:callback(message)


def digest(value):
    # A single C JSON encoder call over a large snapshot holds the GIL long
    # enough to stall Qt, even on a worker. Stream bounded chunks instead.
    if isinstance(value, dict) and value.get('schema') in ('kc-bookshelf-export/v2','kc-metadata-fingerprint/v1'):
        hasher = hashlib.sha256()
        encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        parts, length = [], 0
        for part in encoder.iterencode(value):
            parts.append(part); length += len(part)
            if length >= 32768:
                checkpoint()
                hasher.update(''.join(parts).encode('utf-8'))
                parts, length = [], 0
        hasher.update(''.join(parts).encode('utf-8'))
        return hasher.hexdigest()
    return hashlib.sha256(canonical(value)).hexdigest()


def loads(raw):
    checkpoint()
    if len(raw) > MAX_JSON:
        raise Invalid('JSON 超过 64 MiB，未读取正文或修改设备')
    def pairs(items):
        checkpoint()
        out = {}
        for key, val in items:
            if key in out:
                raise Invalid('JSON 存在重复键：' + key)
            out[key] = val
        return out
    def constant(value):
        raise Invalid('JSON 非有限数值：' + value)
    try:
        return json.loads(raw.decode('utf-8-sig') if isinstance(raw, bytes) else raw,
                          object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise Invalid(str(exc)) from exc


def obj(**props):
    return dict(type='object', properties=props, required=list(props), additionalProperties=False)


def arr(item, maximum=200000, unique=False):
    return dict(type='array', items=item, maxItems=maximum, uniqueItems=unique)


TEXT = dict(type='string', maxLength=8192)
ID = dict(type='string', minLength=1, maxLength=256, pattern=r'^[^\x00-\x1f/\\]+$')
NAME = dict(type='string', minLength=1, maxLength=1024, pattern=r'^[^\x00-\x1f]+$')
HASH = dict(type='string', pattern=r'^[0-9a-f]{64}$')
INTEGER = dict(type='integer', minimum=0)
BOOL = dict(type='boolean')
NULLTEXT = dict(anyOf=[TEXT, dict(type='null')])
NULLINT = dict(anyOf=[dict(type='integer'), dict(type='null')])
DEVICE = obj(instance_id=ID, storage_uuid=NULLTEXT, serial_sha256=NULLTEXT)
POLICY = obj(version=INTEGER, protected_collections=arr(ID, unique=True),
             max_members=INTEGER, max_request_bytes=INTEGER,
             max_creates=INTEGER, max_rename_delete=INTEGER)
CAPS = obj(version=ID, verified_operations=arr(dict(enum=list(KINDS)), unique=True), evidence=TEXT)
BOOK = obj(uuid=ID, title=NULLTEXT, location=NULLTEXT, cde_key=NULLTEXT, cde_type=NULLTEXT,
           calibre_uuid=NULLTEXT, metadata_matches=INTEGER, collection_count=NULLINT)
RELATION = obj(collection_uuid=NULLTEXT, book_uuid=NULLTEXT, member_type=NULLTEXT,
               member_key=NULLTEXT, member_present=NULLINT, sideloaded=NULLINT, order=NULLINT)
COLLECTION = obj(uuid=ID, name=TEXT, complete=BOOL)
ARGS = {
    'create_collection': obj(name=NAME),
    'add_members': obj(members=arr(ID, unique=True)),
    'remove_members': obj(members=arr(ID, unique=True)),
    'rename_collection': obj(name=NAME),
    'delete_collection': obj(),
    'verify_state': obj(),
}
OP = dict(oneOf=[obj(op_id=ID, kind=dict(const=kind), collection_uuid=ID,
                        before=dict(anyOf=[HASH, dict(type='null')]), after=dict(anyOf=[HASH, dict(type='null')]),
                        args=args, depends_on=arr(ID, unique=True), reason=TEXT)
                    for kind, args in ARGS.items()])
ISSUE = obj(code=ID, message=TEXT, collection_uuid=NULLTEXT)
SCHEMAS = {
    'kc-policy-request/v1': obj(schema=dict(const='kc-policy-request/v1'),job_id=ID,
        device=DEVICE,kind=dict(const='set_protection_policy'),before_version=INTEGER,policy=POLICY,request_digest=HASH),
    'kc-bookshelf-export/v2': obj(
        schema=dict(const='kc-bookshelf-export/v2'), snapshot_id=ID, generated_utc=TEXT,
        device=DEVICE, firmware=TEXT, capabilities=CAPS, policy=POLICY,
        mapping=obj(stable=BOOL, calibre_library_uuid=NULLTEXT, metadata_sha256=TEXT),
        books=arr(BOOK), collections=arr(COLLECTION), relations=arr(RELATION)),
    'kc-edit-plan/v1': obj(
        schema=dict(const='kc-edit-plan/v1'), plan_id=ID, device=DEVICE, library_uuid=ID,
        snapshot_digest=HASH, inputs_digest=HASH, policy_version=INTEGER,
        capabilities_version=ID, scope=arr(ID, unique=True),
        operations=arr(OP, 20000), blockers=arr(ISSUE), plan_digest=HASH),
    'kc-edit-request/v1': obj(
        schema=dict(const='kc-edit-request/v1'), job_id=ID, plan_id=ID,
        plan_digest=HASH, device=DEVICE, library_uuid=ID, snapshot_digest=HASH,
        policy_version=INTEGER, capabilities_version=ID,
        operations=arr(OP, 20000), request_digest=HASH),
    'kc-edit-result/v1': obj(
        schema=dict(const='kc-edit-result/v1'), job_id=ID, request_digest=HASH,
        device=DEVICE, operations=arr(obj(op_id=ID, status=dict(enum=[
            'confirmed', 'pending', 'conflict', 'failed', 'not_run']),
            observed_digest=dict(anyOf=[HASH, dict(type='null')]), message=TEXT), 20000),
        snapshot_id=NULLTEXT, result_digest=HASH),
}

# Version 1 remains byte-compatible for already indexed books.
SCHEMAS['kc-edit-request/v2']=deepcopy(SCHEMAS['kc-edit-request/v1'])
SCHEMAS['kc-edit-request/v2']['properties'].update(schema=dict(const='kc-edit-request/v2'),new_books=arr(obj(alias=ID,location=TEXT,size=INTEGER,sha256=HASH)))
SCHEMAS['kc-edit-request/v2']['required'].append('new_books')
SCHEMAS['kc-edit-result/v2']=deepcopy(SCHEMAS['kc-edit-result/v1'])
SCHEMAS['kc-edit-result/v2']['properties'].update(schema=dict(const='kc-edit-result/v2'),book_bindings=arr(obj(alias=ID,uuid=ID)))
SCHEMAS['kc-edit-result/v2']['required'].append('book_bindings')


def check(spec, value, path='$'):
    """Small validator for precisely the JSON Schema subset declared above."""
    for union in ('anyOf', 'oneOf'):
        if union in spec:
            n = 0
            for child in spec[union]:
                try:
                    check(child, value, path)
                    n += 1
                except Invalid:
                    pass
            if (union == 'oneOf' and n != 1) or not n:
                raise Invalid(path + ': 类型、操作或字段不符合协议')
            return
    if 'const' in spec and value != spec['const']:
        raise Invalid(path + ': 常量不匹配')
    if 'enum' in spec and value not in spec['enum']:
        raise Invalid(path + ': 不支持的值')
    kind = spec.get('type')
    types = {'object': dict, 'array': list, 'string': str, 'integer': int,
             'boolean': bool, 'null': type(None)}
    if kind and type(value) is not types[kind]:
        raise Invalid(path + ': 类型错误')
    if kind == 'object':
        if set(value) != set(spec['required']):
            raise Invalid(path + ': 缺失字段或出现未知字段')
        for key, val in value.items():
            check(spec['properties'][key], val, path + '.' + key)
    elif kind == 'array':
        if len(value) > spec['maxItems']:
            raise Invalid(path + ': 数量超限')
        if spec.get('uniqueItems') and len({canonical(v) for v in value}) != len(value):
            raise Invalid(path + ': 重复项')
        for i, val in enumerate(value):
            check(spec['items'], val, f'{path}[{i}]')
    elif kind == 'string':
        if not spec.get('minLength', 0) <= len(value) <= spec.get('maxLength', MAX_JSON):
            raise Invalid(path + ': 长度错误')
        if 'pattern' in spec and re.fullmatch(spec['pattern'], value) is None:
            raise Invalid(path + ': 字符格式错误')
    elif kind == 'integer' and value < spec.get('minimum', value):
        raise Invalid(path + ': 数值越界')


def seal(value, field):
    result = deepcopy(value)
    result.pop(field, None)
    result[field] = digest(result)
    return result


def compile_check(spec):
    """Compile the fixed schema once, rather than interpret it for every edge.

    Same checks as check(); field paths are constructed only on errors. This is
    material for 100k relationships, with no weakening of input validation.
    """
    if 'anyOf' in spec or 'oneOf' in spec:
        key = 'anyOf' if 'anyOf' in spec else 'oneOf'
        children = spec[key]
        validators = [compile_check(s) for s in children]
        types = {'string':str,'null':type(None),'integer':int,'boolean':bool,'object':dict,'array':list}
        simple_types = [types.get(s.get('type')) for s in children]
        if None not in simple_types and len(set(simple_types)) == len(simple_types):
            choices = dict(zip(simple_types,validators))
            def check_union(value):
                fn = choices.get(type(value))
                if fn is None: raise Invalid('类型不符合协议')
                fn(value)
            return check_union
        def check_union(value):
            valid = 0
            for fn in validators:
                try: fn(value); valid += 1
                except Invalid: pass
            if not valid or (key == 'oneOf' and valid != 1):
                raise Invalid('未知操作或字段不符合协议')
        return check_union
    if 'const' in spec:
        expected = spec['const']
        def check_const(value):
            if value != expected or type(value) is not type(expected): raise Invalid('常量不匹配')
        return check_const
    if 'enum' in spec:
        choices = tuple(spec['enum'])
        def check_enum(value):
            if value not in choices: raise Invalid('不支持的值')
        return check_enum
    kind = spec.get('type')
    if kind == 'object':
        fields = [(k,compile_check(s)) for k,s in spec['properties'].items()]
        keys = spec['properties'].keys()
        def check_object(value):
            if type(value) is not dict or value.keys() != keys: raise Invalid('对象缺失字段或包含未知字段')
            for field,fn in fields:
                try: fn(value[field])
                except Invalid as exc: raise Invalid(field + ': ' + str(exc)) from exc
        return check_object
    if kind == 'array':
        child = compile_check(spec['items']); maximum = spec['maxItems']; unique = spec.get('uniqueItems')
        def check_array(value):
            if type(value) is not list or len(value) > maximum: raise Invalid('列表类型或数量错误')
            if unique and len({canonical(v) for v in value}) != len(value): raise Invalid('列表存在重复项')
            for i,item in enumerate(value):
                try: child(item)
                except Invalid as exc: raise Invalid(f'[{i}]: {exc}') from exc
                if i and not i % 2048:
                    # Bound worker monopolization during deeply nested checks;
                    # yielding does not skip validation or alter global GIL settings.
                    checkpoint()
                    time.sleep(.001)
        return check_array
    if kind == 'string':
        minimum,maximum = spec.get('minLength',0),spec.get('maxLength',MAX_JSON)
        pattern = re.compile(spec['pattern']) if 'pattern' in spec else None
        def check_string(value):
            if type(value) is not str or not minimum <= len(value) <= maximum: raise Invalid('字符串类型或长度错误')
            if pattern and pattern.fullmatch(value) is None: raise Invalid('字符格式错误')
        return check_string
    if kind == 'integer':
        minimum = spec.get('minimum')
        def check_integer(value):
            if type(value) is not int or (minimum is not None and value < minimum): raise Invalid('整数类型或范围错误')
        return check_integer
    expected = {'null':type(None),'boolean':bool}[kind]
    def check_type(value):
        if type(value) is not expected: raise Invalid('类型不符合协议')
    return check_type


VALIDATORS = {name:compile_check(spec) for name,spec in SCHEMAS.items()}


def validate(value):
    checkpoint()
    if not isinstance(value, dict) or value.get('schema') not in SCHEMAS:
        raise Invalid('未知或缺失的 KC 协议版本')
    VALIDATORS[value['schema']](value)
    field = {'kc-policy-request/v1':'request_digest','kc-edit-plan/v1': 'plan_digest', 'kc-edit-request/v1': 'request_digest',
             'kc-edit-request/v2':'request_digest','kc-edit-result/v2':'result_digest','kc-edit-result/v1': 'result_digest'}.get(value['schema'])
    if field and seal(value, field)[field] != value[field]:
        raise Invalid('内容摘要不匹配：' + field)
    if value['schema'] == 'kc-bookshelf-export/v2':
        book_ids = [b['uuid'] for b in value['books']]
        col_ids = [c['uuid'] for c in value['collections']]
        if len(set(book_ids + col_ids)) != len(book_ids) + len(col_ids):
            raise Invalid('重复的设备对象 UUID')
        books = set(book_ids)
        bad, seen = set(), set()
        for r in value['relations']:
            pair = (r['collection_uuid'], r['book_uuid'])
            if r['book_uuid'] not in books or pair in seen:
                bad.add(r['collection_uuid'])
            seen.add(pair)
        for c in value['collections']:
            if c['complete'] and c['uuid'] in bad:
                raise Invalid('快照错误地把未知或重复成员标为完整')
    elif 'operations' in value:
        if value['schema']=='kc-edit-request/v2':
            from pathlib import PurePosixPath
            books=value['new_books'];aliases={b['alias'] for b in books}
            if not books or len(books)>20000 or len(aliases)!=len(books) or len({b['location'] for b in books})!=len(books):raise Invalid('新书清单为空或存在重复')
            for b in books:
                path=b['location']
                if not re.fullmatch(r'kc-new-[0-9a-f]{64}',b['alias']) or not path.startswith('/mnt/us/documents/') or '..' in PurePosixPath(path).parts or str(PurePosixPath(path))!=path or any(ord(c)<32 or c=='\\' for c in path):raise Invalid('新书路径或标识无效')
            if any(u.startswith('kc-new-') and u not in aliases for o in value['operations'] for u in o.get('args',{}).get('members',[])):raise Invalid('新书描述缺失')
        seen = set()
        for op in value['operations']:
            if op['op_id'] in seen:
                raise Invalid('重复操作 ID')
            if 'depends_on' in op and not set(op['depends_on']).issubset(seen):
                raise Invalid('依赖缺失、前向依赖或循环依赖')
            seen.add(op['op_id'])
    return value
