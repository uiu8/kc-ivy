"""One source-to-plan pipeline and confirmed-only profile advancement."""
from collections import defaultdict
from copy import deepcopy
from uuid import uuid4
from .protocol import Invalid, digest, checkpoint
from .planner import Catalog,intent,plan
from .metadata import mappings,assert_library
from .ledger import empty_ledger,adopt,edge_key
from .rules import evaluate,DEFAULT_SETTINGS,match


def adopt_profile(snapshot,metadata,library,field,selected_copies=None,include_current=False):
    assert_library(snapshot,library,metadata)
    cat=Catalog(snapshot)
    available=mappings(snapshot,metadata['rows'])
    selected=available if selected_copies is None else selected_copies
    for uid,copies in selected.items():
        if not copies or not set(copies).issubset(available.get(uid,())):
            raise Invalid('副本采用范围不属于当前唯一映射')
    bindings={name:ids[0] for name,ids in cat.names.items() if len(ids)==1}
    baseline={}
    ledger=empty_ledger(snapshot['device'],library)
    edges=[]
    for row in metadata['rows']:
        if row['uuid'] not in selected: continue
        values=row['fields'].get(field)
        if values is None: raise Invalid('目标列没有完整读取')
        baseline[row['uuid']]=dict(id=row['id'],copies=selected[row['uuid']],values=[] if include_current else values)
        if not include_current:
            for name in values:
                cid=bindings.get(name)
                if cid:
                    edges.extend((cid,b) for b in selected[row['uuid']] if b in cat.members[cid])
    ledger=adopt(ledger,'column:'+field,edges)
    return dict(version=1,revision=0,device=deepcopy(snapshot['device']),library_uuid=library,field=field,
                bindings=bindings,column_baseline=baseline,ledger=ledger,rules=[],settings=deepcopy(DEFAULT_SETTINGS))


def prepare(snapshot,metadata,profile,manual=(),include_sources=True,resolutions=None):
    if snapshot['device']!=profile['device']: raise Invalid('当前设备不属于采用配置')
    cat=Catalog(snapshot)
    resolutions=dict(resolutions or {})
    if any(v not in ('manual','source') for v in resolutions.values()):raise Invalid('未知冲突处理选择')
    conflicts=[]
    # Profiles are immutable inputs. Copy only the name map and touched claims;
    # three full copies of a 100k-edge ledger caused generation-2 GC UI stalls.
    p=dict(profile,bindings=dict(profile['bindings']))
    ledger=p['ledger']; old_claims=ledger['claims']; new_claims=dict(old_claims)
    bindings=p['bindings']; creates=[]; generated=[]; warnings=[]; issues=[]
    rows={r['uuid']:r for r in metadata['rows']}
    if len(rows)!=len(metadata['rows']): raise Invalid('Calibre 书籍 UUID 重复，拒绝来源编辑')
    current_copies=mappings(snapshot,metadata['rows'])
    accepted={uid:[b for b in base['copies'] if b in current_copies.get(uid,())]
              for uid,base in p['column_baseline'].items() if uid in rows}
    accepted={uid:bids for uid,bids in accepted.items() if bids}
    if include_sources:
        extra=sum(len(set(bids)-set(p['column_baseline'].get(uid,{}).get('copies',[]))) for uid,bids in current_copies.items())
        missing=len({uid for uid,base in p['column_baseline'].items() if base['copies']}-set(accepted))
        if extra:warnings.append(f'{extra} 个设备副本未纳入当前来源范围；不会自动扩大管理权限')
        if missing:warnings.append(f'{missing} 本已采用书籍本次缺少稳定映射；已有关系保留')
    book_scope={b for bids in accepted.values() for b in bids}
    legacy_names={}
    for c in snapshot['collections']:
        if ',' in c['name']:legacy_names.setdefault(c['name'].replace(',',';'),[]).append(c['uuid'])
    touched_baselines=defaultdict(list)
    def resolve(name):
        if name in bindings:
            cid=bindings[name]
            if cid in ledger['tombstones']:
                warnings.append('已删除的输出保持排除：'+name); return None
            if cid not in cat.collections and not any(i['collection_uuid']==cid for i in creates):
                issues.append('已绑定收藏夹不存在，不能自动重建：'+name); return None
            return cid
        candidates=cat.names.get(name,[])
        if not candidates and name in legacy_names:
            issues.append('可能是旧回填转换的名称「'+name+'」；请先校准初始状态并确认名称对应，不自动新建。')
            return None
        if not candidates and p['settings']['ignore_case']:
            candidates=[c['uuid'] for c in snapshot['collections'] if c['name'].casefold()==name.casefold()]
        if len(candidates)>1: issues.append('同名收藏夹需要 UUID 绑定：'+name); return None
        cid=candidates[0] if candidates else str(uuid4())
        bindings[name]=cid
        if not candidates: creates.append(intent('create_collection',cid,dict(name=name),'来源生成新收藏夹'))
        return cid
    def set_source(source,desired,scope):
        for key in tuple(new_claims):
            row=new_claims[key]
            if row['book_uuid'] in scope and source in row['sources']:
                row=dict(row,sources=list(row['sources']));new_claims[key]=row
                row['sources'].remove(source)
                if not row['sources']: del new_claims[key]
        for cid,bid in desired:
            key=edge_key(cid,bid)
            if cid in ledger['tombstones'] or key in ledger['exclusions']:
                warnings.append('来源输出被明确排除：'+cid+'/'+bid); continue
            row=dict(new_claims.get(key,dict(collection_uuid=cid,book_uuid=bid,sources=[])))
            row['sources']=sorted(set(row['sources'])|{source})
            new_claims[key]=row
    if include_sources:
        assert_library(snapshot,p['library_uuid'],metadata)
        source='column:'+p['field']
        desired={(r['collection_uuid'],r['book_uuid']) for r in old_claims.values()
                 if source in r['sources'] and r['book_uuid'] in book_scope}
        for uid,bids in accepted.items():
            base=p['column_baseline'][uid]; current=set(rows[uid]['fields'][p['field']]); before=set(base['values'])
            for bid,names in base.get('pending_copies',{}).items():
                if bid not in bids:continue
                for name in set(names)&current:
                    cid=resolve(name)
                    if cid:
                        desired.add((cid,bid))
                        touched_baselines[cid].append(dict(uuid=uid,name=name,present=True,copy_uuid=bid))
            for name in current-before:
                cid=resolve(name)
                if cid:
                    desired.update((cid,b) for b in bids)
                    touched_baselines[cid].append(dict(uuid=uid,name=name,present=True))
            for name in before-current:
                cid=bindings.get(name)
                if cid and cid not in ledger['tombstones']:
                    # Rename aliases may resolve to the same UUID. Replacing
                    # an old spelling with its new one must not withdraw it.
                    if not any(bindings.get(n)==cid for n in current):
                        desired.difference_update((cid,b) for b in bids)
                    touched_baselines[cid].append(dict(uuid=uid,name=name,present=False))
        set_source(source,desired,book_scope)
        evaluated=evaluate(p['rules'],p['settings'],metadata,accepted)
        warnings.extend(evaluated['warnings'])
        for rid,output in evaluated['outputs'].items():
            if output['action']=='delete':
                for name in output['targets']:
                    cid=bindings.get(name)
                    if cid and cid in cat.collections:
                        generated.append(intent('delete_collection',cid,reason='显式 Delete 规则候选'))
                    else: warnings.append('Delete 输出没有已绑定对象：'+name)
                continue
            desired=set(); surviving_names=set(output['targets'])
            for name,bids in output['targets'].items():
                cid=resolve(name)
                if cid: desired.update((cid,b) for b in bids)
            # Minimum and disappeared entire outputs preserve old claims until an
            # explicit cleanup action; field-value removal inside a surviving
            # output can withdraw its adopted source claim.
            active_cids={bindings[name] for name in surviving_names if name in bindings}
            existing={(r['collection_uuid'],r['book_uuid']) for r in old_claims.values()
                      if 'rule:'+rid in r['sources'] and r['book_uuid'] in book_scope
                      and r['collection_uuid'] not in active_cids}
            filtered={bid for uid in output['filtered'] for bid in accepted.get(uid,())}
            set_source('rule:'+rid,desired|existing,book_scope-filtered)
    # Resolve only explicitly chosen manual removals. Do not persist exclusions
    # during preview; their effects commit only after device confirmation.
    adjusted_manual=[]
    for op in manual:
        if op['kind']!='remove_members':adjusted_manual.append(op);continue
        cid=op['collection_uuid']; remaining=[]
        for bid in op['args']['members']:
            key=edge_key(cid,bid);choice=resolutions.get(op['intent_id']+'/'+bid)
            collision=key in new_claims and key not in old_claims
            if collision:
                conflicts.append(dict(key=op['intent_id']+'/'+bid,collection_uuid=cid,book_uuid=bid,choice=choice))
                if choice=='source':continue
                if choice=='manual':new_claims.pop(key,None)
            remaining.append(bid)
        if remaining:
            adjusted=deepcopy(op);adjusted['args']['members']=remaining;adjusted_manual.append(adjusted)
    effects={}; additions=defaultdict(list); removals=defaultdict(list); source_only=defaultdict(list)
    for key in set(old_claims)|set(new_claims):
        old,new=old_claims.get(key),new_claims.get(key)
        if old==new: continue
        row=new or old; cid=row['collection_uuid']
        (additions if old is None else removals if new is None else source_only)[cid].append((key,row['book_uuid']))
    for op in creates:
        effects[op['intent_id']]=dict(bindings={name:cid for name,cid in bindings.items() if cid==op['collection_uuid']})
    intents=list(creates)
    last_for={}
    for kind,groups in (('add_members',additions),('remove_members',removals),('verify_state',source_only)):
        for cid,values in sorted(groups.items()):
            args=dict(members=sorted({b for _,b in values})) if kind!='verify_state' else {}
            op=intent(kind,cid,args,'已采用来源的共同归属差异')
            intents.append(op); last_for[cid]=op['intent_id']
            effects[op['intent_id']]=dict(claims={key:new_claims.get(key) for key,_ in values})
    for cid,changes in touched_baselines.items():
        if cid not in last_for:
            op=intent('verify_state',cid,reason='状态不变，核验后推进列基线')
            intents.append(op); last_for[cid]=op['intent_id']
        # Advance per collection only after its last source operation confirms.
        effects.setdefault(last_for[cid],{})['baseline']=changes
    source_ops=list(intents)
    for op in list(generated)+adjusted_manual:
        cid=op['collection_uuid']; kind=op['kind']; effect={}
        if kind=='remove_members':
            for bid in op['args']['members']:
                if bid in {b for _,b in additions.get(cid,[])}:
                    issues.append('手动移除与本次来源新增冲突：收藏夹「'+cat.collections.get(cid,{}).get('name',cid)+'」 / 书籍「'+(cat.books.get(bid,{}).get('title') or bid)+'」。请撤销这条移除草稿，或先调整指定列/规则，使来源不再要求加入，再生成预览。')
            effect['exclude']=[edge_key(cid,b) for b in op['args']['members']]
            effect['claims']={key:None for key in effect['exclude']}
        if kind=='add_members':
            if set(op['args']['members']) & {b for _,b in removals.get(cid,[])}:
                issues.append('手动加入与本次来源移除冲突：'+cid)
            effect['unexclude']=[edge_key(cid,b) for b in op['args']['members']]
        if kind=='delete_collection':
            if any(i['collection_uuid']==cid for i in source_ops): issues.append('删架与本次来源输出冲突：'+cid)
            effect['tombstone']=cid
            effect['claims']={key:None for key,row in new_claims.items() if row['collection_uuid']==cid}
        if kind=='rename_collection': effect['rename']=dict(uuid=cid,name=op['args']['name'])
        if kind=='create_collection': effect['bindings']={op['args']['name']:cid}
        effects[op['intent_id']]=effect; intents.append(deepcopy(op))
    scope={i['collection_uuid'] for i in intents if i['kind']!='create_collection'}
    aliases=defaultdict(dict)
    for name,cid in bindings.items():aliases[cid][name]=cid
    for op in intents:
        effects.setdefault(op['intent_id'],{}).setdefault('bindings',{}).update(aliases[op['collection_uuid']])
    for op in intents:
        name=cat.collections.get(op['collection_uuid'],{}).get('name',op['args'].get('name',''))
        if op['kind']!='verify_state' and match(name,p['settings']['ignore_all'],p['settings']['ignore_case']):
            issues.append('全局保护名称规则阻止修改：'+name)
    inputs=dict(metadata=metadata['fingerprint'],profile_revision=profile['revision'],manual=list(manual),include_sources=include_sources)
    if resolutions:inputs['resolutions']=resolutions
    preview=plan(snapshot,intents,scope,profile['library_uuid'],inputs)
    return dict(plan=preview,effects=effects,inputs=inputs,issues=issues,warnings=warnings,conflicts=conflicts,
                submitted={uid:row['fields'].get(profile['field'],[]) for uid,row in rows.items()},
                snapshot=snapshot,profile_key=None)


def apply_effects(profile,job,confirmed):
    p=dict(profile,bindings=dict(profile['bindings']),column_baseline=dict(profile['column_baseline']),
        ledger=dict(profile['ledger'],claims=dict(profile['ledger']['claims']),
            exclusions=list(profile['ledger']['exclusions']),tombstones=list(profile['ledger']['tombstones'])))
    for op in confirmed:
        effect=job['effects'].get(op['op_id'],{})
        p['bindings'].update(effect.get('bindings',{}))
        for key,row in effect.get('claims',{}).items():
            if row is None: p['ledger']['claims'].pop(key,None)
            else: p['ledger']['claims'][key]=deepcopy(row)
        p['ledger']['exclusions']=sorted(set(p['ledger']['exclusions'])|set(effect.get('exclude',[])))
        p['ledger']['exclusions']=sorted(set(p['ledger']['exclusions'])-set(effect.get('unexclude',[])))
        if 'tombstone' in effect:
            p['ledger']['tombstones']=sorted(set(p['ledger']['tombstones'])|{effect['tombstone']})
        for change in effect.get('baseline',[]):
            base=dict(p['column_baseline'][change['uuid']]);p['column_baseline'][change['uuid']]=base; values=set(base['values'])
            if change['present']: values.add(change['name'])
            else: values.discard(change['name'])
            base['values']=sorted(values)
            if base.get('pending_copies'):
                pending={bid:list(names) for bid,names in base['pending_copies'].items()}
                for bid in list(pending):
                    if not change.get('copy_uuid') or change['copy_uuid']==bid:
                        pending[bid]=[name for name in pending[bid] if name!=change['name']]
                        if not pending[bid]:del pending[bid]
                base['pending_copies']=pending
        if 'rename' in effect:
            rename=effect['rename']; p['bindings'][rename['name']]=rename['uuid']
    from .deferred import remap_profile
    p=remap_profile(p,job.get('result',{}).get('book_bindings',[]))
    return p
