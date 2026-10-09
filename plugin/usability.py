"""Pure user-facing recovery, state and preview descriptions."""
from copy import deepcopy
from .protocol import Invalid


def uses_column(profile):
    return bool(profile.get('field')) and profile.get('sync_mode','column')!='manual'


def task_actions(job):
    results=job.get('result',{}).get('operations',[])
    confirmed={r['op_id'] for r in results if r['status']=='confirmed'}
    outstanding=any(o['kind']!='verify_state' and o['op_id'] not in confirmed for o in job['request']['operations'])
    uncertain=any(r['status']=='pending' for r in results) or job.get('status')=='pending'
    staged=job.get('status')=='staged'
    return dict(check=True,recover=outstanding and not uncertain and
                (bool(results) or staged and not job.get('publication_verified')),
                retry=staged and not results,cancel=staged and not results and not job.get('publication_verified'))


def pending_operations(job,snapshot):
    """Display only: never apply sent operations to the device snapshot."""
    from .protocol import digest
    if not job:return []
    if job.get('status')=='closed' and not any(r['status']=='pending' for r in job.get('result',{}).get('operations',[])):return []
    results={r['op_id']:r['status'] for r in job.get('result',{}).get('operations',[])}
    stale=job['request']['snapshot_digest']==digest(snapshot)
    out=[]
    for op in job['request']['operations']:
        if op['kind']=='verify_state':continue
        state=results.get(op['op_id'])
        if state=='confirmed':
            if not stale:continue
            label='已执行，待刷新'
        elif state=='pending':label='结果待核验'
        elif state:label='执行未完成'
        else:label='等待设备执行' if job.get('publication_verified') else '发送待核对'
        out.append(dict(op,label=label))
    return out

def task_status(job,state):
    results=job.get('result',{}).get('operations',[])
    if results:
        confirmed={r['op_id'] for r in results if r['status']=='confirmed'}
        if any(r['status']=='pending' for r in results):return '结果待核验','安全弹出，在 Kindle 运行原任务核验；不要重发'
        expected={o['op_id'] for o in job['request']['operations']}
        if job.get('status')=='closed':return '已关闭（历史结果）',f'原任务成功 {len(confirmed)}/{len(expected)} 项；不会自动重试，可在任务记录查看详情'
        if confirmed!=expected:return ('部分成功' if confirmed else '未完成'),'先刷新并读取设备状态，再恢复未完成项为草稿'
        if 'column_field' in job and not job['column_field']:return '已完成（无需列回填）','无需重发，可继续在 kc-ivy 整理'
        if state.column_pending(job['request']['job_id']):return '执行完成，回填待处理','在列值导入与回填中处理已确认结果'
        changed={o['op_id'] for o in job['request']['operations'] if o['kind']!='verify_state'}
        if changed.issubset(state.baseline_done(job['request']['job_id'])):
            return '执行及列值处理完成','无需重发，可继续整理'
        return '设备执行完成','读取最新快照以处理列值；未纳管书籍不一定有对应列值'
    if job.get('status')=='closed':return '已关闭原记录','如已恢复草稿，请检查草稿并重新预览'
    if job.get('publication_verified'):return '已发送，等待执行回执','安全弹出 → Kindle 执行任务 → 重连读取结果'
    return '发送状态待核对','核对原任务传输；没有回执不代表未发送'

def recovery_items(values,snapshot,old_snapshot):
    books={b['uuid']:b for b in snapshot['books']};old={b['uuid']:b for b in old_snapshot['books']}
    return [dict(intent=v,missing=[dict(uuid=u,title=old.get(u,{}).get('title') or u)
                                 for u in v['args'].get('members',[]) if u not in books]) for v in values]

def choose_recovery(items,selected,skip_missing=False):
    values=[]
    for item in items:
        v=deepcopy(item['intent'])
        if v['intent_id'] not in selected:continue
        if item['missing']:
            if not skip_missing:raise Invalid('所选操作含失效书籍；请明确勾选排除失效项，或取消该操作')
            missing={b['uuid'] for b in item['missing']}
            v['args']['members']=[u for u in v['args']['members'] if u not in missing]
            if not v['args']['members']:continue
        values.append(v)
    remaining={v['intent_id'] for v in values}
    if any(set(v['depends_on'])-remaining for v in values):raise Invalid('选择缺少前置操作；请同时选择移动的加入步骤或新建收藏夹步骤')
    return values

def plan_rows(operations,catalog,labels):
    members={k:set(v) for k,v in catalog.members.items()};names={k:v['name'] for k,v in catalog.collections.items()};rows=[]
    for o in operations:
        cid=o['collection_uuid'];kind=o['kind'];args=o['args'];name=names.get(cid,args.get('name',cid));before=len(members.get(cid,set()))
        if kind=='create_collection':names[cid]=args['name'];members[cid]=set();detail='新建空收藏夹'
        elif kind=='add_members':
            members.setdefault(cid,set()).update(args['members']);detail=f'{before} 本 → 加入 {len(args["members"])} 本 → {len(members[cid])} 本；保留其他归属'
        elif kind=='remove_members':
            members.setdefault(cid,set()).difference_update(args['members']);detail=f'{before} 本 → 移除 {len(args["members"])} 本 → {len(members[cid])} 本；保留书籍文件'
        elif kind=='rename_collection':detail=name+' → '+args['name'];names[cid]=args['name']
        elif kind=='delete_collection':detail=f'删除收藏夹；保留 {before} 个成员对应的书籍文件'
        else:detail=f'{before} 本，无需改动'
        rows.append(dict(op_id=o['op_id'],cells=[labels[kind],name,detail,o['reason']]))
    return rows
