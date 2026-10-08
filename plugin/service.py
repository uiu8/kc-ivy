"""Persistent desktop workflow; UI delegates business work to this boundary."""
from copy import deepcopy
from .protocol import Invalid,digest
from .state import StateStore
from .metadata import read_metadata
from .workflow import adopt_profile,prepare,apply_effects
from .planner import plan,request
from .ledger import empty_ledger
from .rules import DEFAULT_SETTINGS


class Service:
    def __init__(self,directory):
        self.state=StateStore(directory);self._cached=None
    def key(self,library,snapshot): return self.state.key(library,snapshot['device'])
    def profile(self,library,snapshot):
        key=self.key(library,snapshot);revision=self.state.revision(key)
        p=self._cached[1] if self._cached and self._cached[0]==key and self._cached[1]['revision']==revision else self.state.get(key)
        if p is None:
            p=dict(version=1,revision=0,device=snapshot['device'],library_uuid=library,
                field='',bindings={},column_baseline={},ledger=empty_ledger(snapshot['device'],library),
                rules=[],settings=deepcopy(DEFAULT_SETTINGS),scope_mode='all',sync_mode='column')
            p=self.state.save(key,p,0)
        self._cached=(key,p)  # one library/device only; revision-checked on every use
        # UI may edit settings before save; isolate those editable structures.
        # Large claims remain read-only inputs to copy-on-write prepare().
        return dict(p,settings=deepcopy(p['settings']),rules=deepcopy(p['rules']),
                    bindings=dict(p['bindings']),column_baseline=dict(p['column_baseline']))
    @staticmethod
    def metadata(api,profile):
        if profile.get('sync_mode')=='manual':
            return dict(rows=[],user_categories={},fingerprint=digest([]))
        if profile.get('sync_mode')=='column' and not profile.get('field'):
            raise Invalid('请先在 KC++“同步设置 → 书架与同步设置”选择原生标签或多值文本自定义列。需要专用列时，可在 Calibre“首选项 → 添加自定义栏目”创建。')
        if not profile['field']:
            if profile['rules']:raise Invalid('规则同步前请先指定往返列并采用书籍范围')
            return dict(rows=[],user_categories={},fingerprint=digest([]))
        fields={r['field'] for r in profile['rules'] if r['action']!='none'}
        if profile['field']: fields.add(profile['field'])
        return read_metadata(api,fields)
    def adopt(self,api,library,snapshot,field,selected_copies,include_current=False):
        from .column_io import assert_field
        assert_field(api,field)
        key=self.key(library,snapshot); old=self.profile(library,snapshot)
        if self.state.job_summaries(key) or old['field']:
            raise Invalid('已有采用记录或任务，不能用重新采用覆盖基线')
        metadata=read_metadata(api,[field])
        p=adopt_profile(snapshot,metadata,library,field,selected_copies,include_current)
        if include_current:
            from .baseline import align
            p,_=align(snapshot,metadata,p)
        p['rules']=old['rules'];p['settings']=old['settings']
        return self.state.save(key,p,old['revision'])
    def adopt_new_books(self,api,library,snapshot):
        from .metadata import mappings
        key=self.key(library,snapshot);p=self.profile(library,snapshot)
        from .usability import uses_column
        if not uses_column(p):raise Invalid('请先在同步设置中启用 Calibre 书架列模式')
        m=self.metadata(api,p);copies=mappings(snapshot,m['rows']);count=0
        for row in m['rows']:
            uid=row['uuid']
            if uid in copies and uid not in p['column_baseline']:
                p['column_baseline'][uid]=dict(id=row['id'],copies=copies[uid],values=[]);count+=1
        if count:
            from .baseline import align
            p,_=align(snapshot,m,p)
            p=self.state.save(key,p,p['revision'])
        return count
    def baseline_preview(self,api,library,snapshot,legacy_names=True):
        from .baseline import align
        p=self.profile(library,snapshot)
        from .usability import uses_column
        if not uses_column(p):raise Invalid('纯 KC++ 模式无需校准列；如需使用列，请先在同步设置中启用')
        if any(j['status'] not in ('complete','closed') for j in self.state.job_summaries(self.key(library,snapshot))):
            raise Invalid('有待发送或待确认任务，请先完成原任务再校准')
        m=self.metadata(api,p);updated,rows=align(snapshot,m,p,legacy_names=legacy_names)
        return dict(profile=updated,rows=rows,fingerprint=m['fingerprint'],revision=p['revision'],snapshot=digest(snapshot),legacy_names=legacy_names)
    def baseline_apply(self,api,library,snapshot,expected):
        current=self.baseline_preview(api,library,snapshot,legacy_names=expected.get('legacy_names',False))
        if any(current[k]!=expected[k] for k in ('fingerprint','revision','snapshot')):
            raise Invalid('基线校准预览已过期，请重新预览')
        if current['rows']:self.state.save(self.key(library,snapshot),current['profile'],current['revision'])
        return len(current['rows'])
    def preview(self,api,library,snapshot,manual,resolutions=None):
        if any(self.state.column_pending(j['job_id']) for j in self.state.job_summaries(self.key(library,snapshot))):
            raise Invalid('有未完成的列回填，请先读取设备结果或恢复回填，再生成新任务')
        p=self.profile(library,snapshot); m=self.metadata(api,p)
        from .usability import uses_column
        if uses_column(p) and p.get('scope_mode','selected')=='all':
            from .scope import expand
            expanded,count=expand(snapshot,m,p)
            if count:
                key=self.key(library,snapshot)
                if any(j['status'] not in ('complete','closed') for j in self.state.job_summaries(key)):
                    raise Invalid('请先处理上次任务，再为新匹配书籍生成预览')
                p=self.state.save(key,expanded,p['revision'])
        value=prepare(snapshot,m,p,manual,include_sources=uses_column(p),resolutions=resolutions)
        value['profile_key']=self.key(library,snapshot)
        return value
    def configure(self,api,library,snapshot,field,selected,rules,settings,revision,fingerprint,scope_mode='selected'):
        """Change scope without interpreting released claims as device removals."""
        from .column_io import assert_field
        from .baseline import align
        from .rules import evaluate,match
        assert_field(api,field)
        key=self.key(library,snapshot);old=self.profile(library,snapshot)
        if old['revision']!=revision:raise Invalid('设置已改变，请重新打开设置')
        if any(j['status'] not in ('complete','closed') for j in self.state.job_summaries(key)):
            raise Invalid('有待发送或待确认任务，请先完成或取消原任务')
        metadata=read_metadata(api,[field])
        if metadata['fingerprint']!=fingerprint:raise Invalid('书库已改变，请重新读取设置')
        if scope_mode not in ('selected','all'):raise Invalid('未知书籍范围设置')
        if scope_mode=='all':
            from .scope import available_books
            selected=available_books(snapshot,metadata)
        fresh=adopt_profile(snapshot,metadata,library,field,selected,include_current=True)
        p=deepcopy(old);changed=old['field']!=field
        p['field']=field;p['sync_mode']='column';p['scope_mode']=scope_mode;p['column_baseline']=fresh['column_baseline']
        rows_by_uuid={row['uuid']:row for row in metadata['rows']}
        for uid,base in p['column_baseline'].items():
            previous=old['column_baseline'].get(uid)
            if not changed and previous:
                base['values']=list(previous['values'])
                pending={bid:list(names) for bid,names in previous.get('pending_copies',{}).items() if bid in base['copies']}
                row=rows_by_uuid.get(uid)
                names=sorted(set(previous['values']) & set(row['fields'][field])) if row else []
                if names:
                    for bid in set(base['copies'])-set(previous['copies']):pending[bid]=names
                if pending:base['pending_copies']=pending
        allowed={bid for base in p['column_baseline'].values() for bid in base['copies']}
        for claim in p['ledger']['claims'].values():
            claim['sources']=[s for s in claim['sources'] if claim['book_uuid'] in allowed and not (changed and s.startswith('column:'))]
        p['ledger']['claims']={k:v for k,v in p['ledger']['claims'].items() if v['sources']}
        # Align only newly adopted copies; do not recalibrate existing pending edits.
        subset=deepcopy(p)
        subset['column_baseline']={uid:dict(base,copies=[bid for bid in base['copies'] if changed or bid not in old['column_baseline'].get(uid,{}).get('copies',[])]) for uid,base in p['column_baseline'].items()}
        subset['column_baseline']={uid:b for uid,b in subset['column_baseline'].items() if b['copies']}
        aligned,_=align(snapshot,metadata,subset)
        p['ledger']=aligned['ledger'];p['bindings']=aligned['bindings']
        for uid,base in aligned['column_baseline'].items():
            if changed or uid not in old['column_baseline']:p['column_baseline'][uid]['values']=base['values']
        p['rules']=deepcopy(rules);p['settings']=deepcopy(settings)
        evaluate(rules,settings,self.metadata(api,p),selected)
        match('',settings['ignore_all'],settings['ignore_case'])
        return self.state.save(key,p,revision)
    def configure_manual(self,library,snapshot,revision,settings):
        from .rules import match
        key=self.key(library,snapshot);p=self.profile(library,snapshot)
        if p['revision']!=revision:raise Invalid('设置已改变，请重新打开设置')
        jobs=self.state.job_summaries(key)
        if any(j['status'] not in ('complete','closed') or self.state.column_pending(j['job_id']) for j in jobs):
            raise Invalid('请先完成或取消原任务，并处理待回填结果，再切换使用方式')
        for j in jobs:
            job=self.state.job(j['job_id'])
            if job.get('result') and job.get('column_field'):
                confirmed={r['op_id'] for r in job['result']['operations'] if r['status']=='confirmed'}
                if confirmed-self.state.baseline_done(j['job_id']):raise Invalid('尚有已确认结果未完成列回填，请先处理')
        match('',settings['ignore_all'],settings['ignore_case'])
        p['sync_mode']='manual';p['settings']=deepcopy(settings)
        return self.state.save(key,p,revision)
    def submit(self,api,library,store,prepared,manual,resolutions=None):
        snap=store.snapshot(); key=self.key(library,snap); p=self.state.get(key)
        if not p or key!=prepared['profile_key'] or prepared['issues']: raise Invalid('预览或采用范围无效')
        m=self.metadata(api,p)
        from .usability import uses_column
        current=dict(metadata=m['fingerprint'],profile_revision=p['revision'],manual=manual,include_sources=uses_column(p))
        if resolutions:current['resolutions']=dict(resolutions)
        req=request(prepared['plan'],snap,digest(current))
        from .deferred import extend_request
        req=extend_request(req,getattr(store,'new_books',[]))
        job={k:v for k,v in prepared.items() if k!='plan'}
        job.update(request=req,status='staged',column_field=p['field'] if uses_column(p) else '')
        self.state.stage(key,job)  # durable before the USB write
        self.publish(store,req)
        return req['job_id']
    def publish(self,store,req):
        jid=req['job_id'];self.state.record_transfer(jid,'attempt')
        try:
            store.send(req)
        except Exception as exc:
            self.state.record_transfer(jid,'error',str(exc));raise
        self.state.record_transfer(jid,'verified_on_device','任务文件已写入并回读一致')
    def retry(self,api,library,store,job):
        # Same bytes / ID only. A new Preview cannot duplicate a staged job.
        if job['request']['library_uuid']!=library:raise Invalid('任务来自其他书库')
        if not store.path('inbox/'+job['request']['job_id']+'.json').exists():
            p=self.profile(library,store.snapshot())
            if job.get('migration_id'):
                from .usability import uses_column
                m=read_metadata(api,[p['field']] if uses_column(p) else [])
            else:m=self.metadata(api,p)
            current=dict(job['inputs'],metadata=m['fingerprint'],profile_revision=p['revision'])
            if digest(current)!=digest(job['inputs']):raise Invalid('原任务尚未发布，但来源已经变化。请取消未发送任务并重新预览')
        self.publish(store,job['request']);return job['request']['job_id']
    def recover_draft(self,library,store,job,close=True):
        from .planner import intent
        from .transport import read
        req=job['request'];jid=req['job_id'];store.check_identity()
        if req['library_uuid']!=library or req['device']!=store.device:raise Invalid('任务书库或设备不匹配')
        if store.path('state/pending.json').exists():raise Invalid('有执行结果不确定的操作，请先在 Kindle 运行执行任务进行核验')
        if list(store.root.rglob('*.partial')):raise Invalid('先恢复快照并在 Kindle 刷新成功，再恢复任务')
        snapshot=store.snapshot()
        if digest(snapshot)==req['snapshot_digest']:raise Invalid('请先在 Kindle 刷新成功并重连，使用新快照恢复草稿')
        result_path=store.path('results/'+jid+'.json')
        if result_path.exists():
            from .ledger import validate_receipt
            result=read(result_path);states=validate_receipt(req,result)
            if not self.state.receipt_known(result):raise Invalid('请先读取设备结果，再恢复剩余操作')
            if any(r['status']=='pending' for r in states.values()):raise Invalid('尚有结果不确定的操作，不能重新生成')
            confirmed={k for k,r in states.items() if r['status']=='confirmed'}
        else:
            if (store.path('inbox/'+jid+'.json').exists() or store.path('state/jobs/'+jid).exists()
                    or (getattr(store,'experimental_mtp',False) and store.pending_transfer(jid))):
                raise Invalid('设备已有任务或执行记录，请运行原任务，不生成重复任务')
            if job.get('publication_verified'):raise Invalid('曾确认发送，但设备任务及回执缺失，需要核查记录；不能自动重发')
            confirmed=set()
        operations=[o for o in req['operations'] if o['op_id'] not in confirmed and o['kind']!='verify_state']
        from .deferred import recovery_bindings
        aliases={b['alias']:b['uuid'] for b in recovery_bindings(req,snapshot,store,operations)}
        values=[intent(o['kind'],o['collection_uuid'],dict(members=[aliases.get(v,v) for v in o['args']['members']]) if 'members' in o['args'] else deepcopy(o['args']),'从原任务恢复，需重新预览') for o in operations]
        ids={o['op_id']:v['intent_id'] for o,v in zip(operations,values)}
        for o,v in zip(operations,values):v['depends_on']=[ids[k] for k in o['depends_on'] if k in ids]
        if close and job['status']=='staged':self.state.close_staged(jid)
        return snapshot,values
    def cancel_unpublished(self,store,job):
        req=job['request'];store.check_identity()
        if store.device!=req['device']:raise Invalid('设备身份不匹配')
        lock=store.path('state/kcpp-transfer.lock');lock.mkdir()
        try:
            target=store.path('inbox/'+req['job_id']+'.json')
            if job.get('publication_verified') or store.path('results/'+req['job_id']+'.json').exists() or store.path('state/jobs/'+req['job_id']).exists() or target.exists() or target.with_name(target.name+'.partial').exists() or store.path('state/pending.json').exists():
                raise Invalid('任务已发布或结果不确定，不能按未发送任务取消')
            self.state.close_staged(req['job_id'])
        finally:lock.rmdir()
    def receive(self,library,snapshot,results):
        key=self.key(library,snapshot); p=self.profile(library,snapshot); messages=[]
        ident='snapshot-'+key;previous=self.state.workspace(ident)
        self.state.save_workspace(dict(id=ident,kind='snapshot',profile=library,snapshot=snapshot),previous['revision'] if previous else 0)
        for result in results:
            from .protocol import validate
            validate(result)
            if result['device']!=snapshot['device']:raise Invalid('回执设备不匹配')
            if self.state.receipt_known(result):continue
            job=self.state.job(result['job_id'])
            if not job:
                messages.append('外部任务回执（不推进本地基线）：'+result['job_id']);continue
            if job['request']['library_uuid']!=library: continue
            p,job,confirmed=self.state.commit_receipt(key,result,apply_effects)
            self._cached=(key,p)
            if confirmed: messages.append(f'确认 {len(confirmed)} 项：'+result['job_id'])
        return p,messages

    def backfill_confirmed(self,api,library,snapshot):
        from . import column_io
        from .protocol import loads
        p=self.profile(library,snapshot);key=self.key(library,snapshot)
        changed=set();messages=[]
        if not p['field']:return [],messages
        for summary in self.state.job_summaries(key):
            job=self.state.job(summary['job_id'])
            if not job.get('result'):continue
            remaining={r['op_id'] for r in job['result']['operations'] if r['status']=='confirmed'}-self.state.column_done(summary['job_id'])
            repair=self.state.column_done(summary['job_id'])-self.state.baseline_done(summary['job_id'])
            if not remaining and not repair:continue
            try:
                field=job.get('column_field')
                if field=='':continue  # Manual-only jobs never write Calibre column values.
                if field is None:
                    # Old jobs did not store the column: require the actual historical profile.
                    with self.state.connect() as db:
                        history=[loads(r[0]) for r in db.execute('SELECT body FROM history WHERE profile=?',(key,))]
                    field=next((v['field'] for v in history+[p] if v['revision']==job['inputs']['profile_revision']),None)
                if field!=p['field']:raise Invalid('原任务的书架列无法确认或已更换，请手动审阅回填')
                if repair:
                    rows=column_io.receipt_plan(p,job,job['result'],read_metadata(api,[field]),repair)
                    p=self.state.accept_column_baseline(key,column_io.accept_backfill(p,rows),summary['job_id'],repair)
                    messages.append('已修复旧任务回填记录，不改列值：'+summary['job_id'])
                if not remaining:continue
                transaction=self.state.column_pending(summary['job_id'])
                if transaction:
                    transaction=deepcopy(transaction)
                    for row in transaction['rows']:
                        current=sorted(api.field_for(field,row['id']) or ())
                        if current not in (sorted(row['before']),sorted(row['after'])):raise Invalid('中断后列值发生变化，需手动处理')
                        row['before']=current
                else:
                    rows=column_io.receipt_plan(p,job,job['result'],read_metadata(api,[field]),remaining)
                    transaction=dict(job_id=summary['job_id'],result_digest=job['result']['result_digest'],operations=sorted(remaining),library_uuid=library,rows=rows,field=field)
                    self.state.stage_column(transaction)
                backup,count=column_io.apply(api,library,field,transaction['rows'],self.state.directory/'column-backups')
                rows=column_io.receipt_plan(p,job,job['result'],read_metadata(api,[field]),remaining)
                p=self.state.accept_column_baseline(key,column_io.accept_backfill(p,rows),summary['job_id'],remaining)
                self.state.finish_column(transaction)
                changed.update(r['id'] for r in transaction['rows'])
                if count:messages.append(f'已自动回填 {count} 本到 {field}；备份：{backup}')
            except Exception as exc:
                messages.append('自动回填停止：'+str(exc));break
        return sorted(changed),messages
