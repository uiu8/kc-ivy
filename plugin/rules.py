"""Original-style rules with explicit outputs; absent output never means delete.

Regex evaluation uses the regex module's timeout, not an uncancellable re call.
Template/user-category values are supplied by the Calibre metadata adapter.
"""
from collections import defaultdict
from copy import deepcopy
from uuid import uuid4
from .protocol import Invalid, checkpoint

DEFAULT_SETTINGS=dict(ignore_case=False,ignore_all=[],keep_kindle_only=True,
    ignore_prefix_suffix=False,reset_collection_times=False,fast_reboot=False,
    default_action='preview',auto_receive=True,auto_backfill=False,
    comparison='device',model='automatic',cover_mode='titles')


def default_rule(field):
    return dict(id=str(uuid4()),field=field,action='create',prefix='',suffix='',minimum=1,
                ignore=[],include=[],rename_from='',rename_to='',split='',template='')


def regex_call(pattern, text, replacement=None, split=False, ignore_case=False):
    if len(pattern)>2048 or len(text)>8192: raise Invalid('规则输入超过长度预算')
    if not pattern: return [text] if split else text if replacement is not None else False
    try:
        import regex
        compiled=regex.compile(pattern,regex.IGNORECASE if ignore_case else 0)
        if split: return compiled.split(text,timeout=.05)
        if replacement is not None: return compiled.sub(replacement,text,timeout=.05)
        return bool(compiled.search(text,timeout=.05))
    except Exception as exc:
        raise Invalid('规则表达式无效或超过 50 ms 预算：'+str(exc)) from exc


def match(value, patterns, ignore_case):
    for pattern in patterns:
        if pattern.startswith('re:'):
            if regex_call(pattern[3:],value,ignore_case=ignore_case): return True
        elif (pattern.casefold()==value.casefold() if ignore_case else pattern==value): return True
    return False


def evaluate(rules, settings, metadata, copies):
    """Returns per-rule name -> device UUID sets, including Delete candidates."""
    outputs, warnings = {}, []
    seen=set()
    for rule in rules:
        checkpoint()
        if set(rule)!=set(default_rule('')) or rule['id'] in seen: raise Invalid('规则字段或 ID 无效')
        seen.add(rule['id'])
        if rule['action'] not in ('none','create','delete'): raise Invalid('未知规则动作')
        if rule['action']=='none': continue
        if type(rule['minimum']) is not int or rule['minimum']<0: raise Invalid('Minimum 必须为非负整数')
        targets=defaultdict(set); cache={}; filtered=set(); filtered_raw=set()
        for row in metadata['rows']:
            checkpoint()
            if row['uuid'] not in copies: continue
            values=row['fields'].get(rule['field'])
            if values is None: raise Invalid('规则来源未完整读取：'+rule['field'])
            for raw in values:
                if raw not in cache:
                    names=[]
                    parts=regex_call(rule['split'],raw,split=True) if rule['split'] else [raw]
                    for part in parts:
                        part=part.strip().replace(';',',')
                        if not part:continue
                        if match(part,rule['ignore'],settings['ignore_case']) or (rule['include'] and not match(part,rule['include'],settings['ignore_case'])):
                            filtered_raw.add(raw);continue
                        renamed=regex_call(rule['rename_from'],part,rule['rename_to'],ignore_case=settings['ignore_case']) if rule['rename_from'] else part
                        name=rule['prefix']+renamed+rule['suffix']
                        if name.strip() and not match(name,settings['ignore_all'],settings['ignore_case']):names.append(name)
                        else:filtered_raw.add(raw)
                    cache[raw]=names
                if not cache[raw] or raw in filtered_raw:filtered.add(row['uuid'])
                for name in cache[raw]: targets[name].update(copies[row['uuid']])
        kept={}
        for name,books in targets.items():
            if len(books)>=rule['minimum']: kept[name]=sorted(books)
            else: warnings.append(dict(rule=rule['id'],name=name,reason='低于 Minimum，保留已有收藏夹'))
        outputs[rule['id']]=dict(action=rule['action'],targets=kept,filtered=sorted(filtered))
    return dict(outputs=outputs,warnings=warnings)


def migrate(document,library_uuid=None,device_uuid=None):
    """Read-only migration with explicit unmapped settings, not silent loss."""
    if isinstance(document,dict) and ('Rows' not in document or 'Settings' not in document):
        library=document.get(library_uuid,{})
        document=library.get(device_uuid,{}) if isinstance(library,dict) else {}
    if not isinstance(document,dict) or 'Rows' not in document or 'Settings' not in document:
        raise Invalid('不是原 Kindle Collections 设置文件')
    rows=document['Rows']; rules=[]; warnings=[]
    if isinstance(rows,dict): rows=[dict(value,field=key) for key,value in rows.items()]
    if not isinstance(rows,list): raise Invalid('原配置 Rows 格式不支持')
    aliases={'Field':'field','Action':'action','Prefix':'prefix','Suffix':'suffix','Minimum':'minimum',
             'Ignore':'ignore','Include':'include','Rename from':'rename_from','Rename to':'rename_to','Split':'split','split_char':'split'}
    for old in rows:
        if not isinstance(old,dict): raise Invalid('原配置含无效规则')
        new=default_rule(old.get('field',old.get('Field','')))
        for key,value in old.items():
            if key=='column': continue  # Original UI label, not the source key.
            target=aliases.get(key,key)
            if target in new and target!='id': new[target]=value
            else: warnings.append('未自动迁移规则字段：'+key)
        new['action']=str(new['action']).lower() or 'none'
        new['minimum']=int(new['minimum'] or 0)
        for key in ('ignore','include'):
            if isinstance(new[key],str): new[key]=[v.strip() for v in new[key].split(',') if v.strip()]
            new[key]=['re:^(?:'+v+')$' for v in new[key] if v]
        if new['split']:
            import re
            new['split']=re.escape(new['split'])
        rules.append(new)
    settings=deepcopy(DEFAULT_SETTINGS)
    for key,value in document['Settings'].items():
        if key in ('ignore_prefix_suffix','reset_collection_times','fast_reboot','ignore_json_db','diff_db_only','kindle_model_version'):
            warnings.append('旧设置不直接执行，原值保存在迁移记录：'+key+'='+str(value))
        elif key=='keep_kindle_only' and not value:
            warnings.append('未启用缺席即删架；请在收藏夹界面明确选择清理候选')
        elif key=='ignore_all':settings[key]=['re:^(?:'+v+')$' for v in value if v]
        elif key in settings: settings[key]=value
        else: warnings.append('未自动迁移全局设置：'+key)
    return dict(rules=rules,settings=settings,warnings=warnings,original=deepcopy(document))
