"""Read original collections JSON without running or depending on its plugin."""
import hashlib
import re
from collections import defaultdict
from uuid import uuid4
from .protocol import Invalid, check, NAME
from .planner import Catalog, intent


def import_collections(document, snapshot, bindings=None, replace=False):
    """Missing keys never mean delete. A bad token blocks its entire target.

    Replacement requires explicit old-name -> device UUID bindings. A path SHA1
    hashes the UTF-8 internal path, never an ebook's bytes.
    """
    if not isinstance(document, dict) or 'schema' in document:
        raise Invalid('此入口需要旧版 collection(s).json 收藏夹数据')
    if any(not isinstance(v, dict) or not isinstance(v.get('items'), list) for v in document.values()):
        raise Invalid('内容不是旧版收藏夹数据；设置文件不能作为收藏关系导入')
    cat = Catalog(snapshot)
    tokens = defaultdict(set)
    for b in snapshot['books']:
        if b['cde_key'] and b['cde_type']:
            tokens['#' + b['cde_key'] + '^' + b['cde_type']].add(b['uuid'])
        if b['location']:
            tokens['*' + hashlib.sha1(b['location'].encode('utf-8')).hexdigest()].add(b['uuid'])
    bindings = bindings or {}
    result, issues, scope, seen = [], [], set(), set()
    for old_name, value in document.items():
        # Locale suffix only; preserve embedded @ in a genuine collection name.
        name = re.sub(r'@[A-Za-z]{2,3}[-_][A-Za-z]{2,4}$', '', old_name)
        check(NAME, name)
        target = bindings.get(old_name)
        if target and target not in cat.collections:
            raise Invalid('导入绑定指向不存在的收藏夹')
        candidates = cat.names.get(name, [])
        if not target and len(candidates) == 1:
            target = candidates[0]
        if not target and candidates:
            issues.append(dict(collection=old_name, reason='同名多个 UUID，需要明确绑定'))
            continue
        if replace and old_name not in bindings:
            issues.append(dict(collection=old_name, reason='替换成员必须明确选择目标收藏夹'))
            continue
        desired, bad = set(), []
        for token in value['items']:
            if not isinstance(token, str):
                bad.append(str(token))
                continue
            if token.startswith('*'):
                token = token.lower()
            candidates = tokens.get(token, set())
            if len(candidates) != 1:
                bad.append(token)
            else:
                desired.update(candidates)
        if bad:
            issues.append(dict(collection=old_name, reason='未匹配或歧义标识，整个架未生成修改', tokens=bad))
            continue
        identity = target or name.casefold()
        if identity in seen:
            issues.append(dict(collection=old_name, reason='多个输入条目指向同一架，需要合并审阅'))
            continue
        seen.add(identity)
        if not target:
            if not desired:  # Merge of an empty old file is not an instruction to create an empty shelf.
                continue
            target = str(uuid4())
            result.append(intent('create_collection', target, dict(name=name), '旧文件导入：明确新增目标'))
        else:
            scope.add(target)
        if desired:
            result.append(intent('add_members', target, dict(members=sorted(desired)), '旧文件导入'))
        if replace:
            remove = cat.members[target] - desired
            if remove:
                result.append(intent('remove_members', target, dict(members=sorted(remove)), '明确替换已绑定收藏夹成员'))
    return dict(intents=result, scope=sorted(scope), issues=issues)
