import pathlib
if not __debug__ and __name__=='__main__':
    exec(compile(pathlib.Path(__file__).read_text(encoding='utf-8'),__file__,'exec',optimize=0),globals());raise SystemExit(0)
import copy,unittest,tempfile,json,time
from tests import snapshot,receipt
from plugin.protocol import digest,Invalid
from plugin.workflow import adopt_profile,prepare,apply_effects
from plugin.planner import intent,request
from plugin.state import StateStore
from plugin.rules import match,migrate,default_rule,evaluate
from plugin.column_io import receipt_plan

def meta(values=None):
    values=values or {'calibre-0':['待读'],'calibre-1':['待读'],'calibre-2':[]}
    rows=[dict(id=i,uuid=u,title=u,fields={'#shelf':v,'tags':v}) for i,(u,v) in enumerate(values.items())]
    return dict(rows=rows,fingerprint=digest(rows),user_categories={})

def job(value,s):
    return dict(request=request(value['plan'],s,value['plan'].data['inputs_digest']),effects=value['effects'],
                snapshot=s,submitted=value['submitted'],status='staged')

class Workflow(unittest.TestCase):
    def test_multi_collection_add_is_not_a_conflict(self):
        m=meta({'calibre-0':['待读','文学'],'calibre-1':['待读'],'calibre-2':[]})
        v=prepare(self.s,m,self.p)
        self.assertFalse(v['issues']);self.assertFalse(v['conflicts'])
        ops=v['plan'].data['operations']
        self.assertTrue(any(o['collection_uuid']=='c2' and o['kind']=='add_members' and 'b0' in o['args']['members'] for o in ops))
        self.assertFalse(any(o['kind']=='remove_members' for o in ops))

    def test_resolved_conflict_does_not_exclude_other_collection(self):
        m=meta({'calibre-0':['待读','文学'],'calibre-1':['待读'],'calibre-2':[]})
        op=intent('remove_members','c2',dict(members=['b0']))
        initial=prepare(self.s,m,self.p,[op]);key=initial['conflicts'][0]['key']
        value=prepare(self.s,m,self.p,[op],resolutions={key:'manual'})
        self.assertFalse(value['issues'])
        j=job(value,self.s);applied=apply_effects(self.p,j,j['request']['operations'])
        from plugin.ledger import edge_key
        self.assertIn(edge_key('c2','b0'),applied['ledger']['exclusions'])
        self.assertNotIn(edge_key('c1','b0'),applied['ledger']['exclusions'])
        self.assertIn(edge_key('c1','b0'),applied['ledger']['claims'])
    def test_explicit_remove_conflict_choices_confirmed_only(self):
        m=meta({'calibre-0':['待读','文学'],'calibre-1':['待读'],'calibre-2':[]})
        op=intent('remove_members','c2',dict(members=['b0']))
        original=copy.deepcopy(self.p)
        blocked=prepare(self.s,m,self.p,[op])
        self.assertTrue(blocked['issues']);key=blocked['conflicts'][0]['key']
        chosen=prepare(self.s,m,self.p,[op],resolutions={key:'manual'})
        self.assertFalse(chosen['issues'])
        self.assertFalse(any(o['kind']=='add_members' and 'b0' in o['args'].get('members',[]) for o in chosen['plan'].data['operations']))
        self.assertEqual(self.p,original)
        j=job(chosen,self.s)
        self.assertEqual(apply_effects(self.p,j,[]),original)
        applied=apply_effects(self.p,j,j['request']['operations'])
        self.assertTrue(applied['ledger']['exclusions'])
        again=prepare(self.s,m,applied)
        self.assertFalse(any(o['kind']=='add_members' and 'b0' in o['args'].get('members',[]) for o in again['plan'].data['operations']))
        source=prepare(self.s,m,self.p,[op],resolutions={key:'source'})
        self.assertFalse(source['issues'])
        self.assertFalse(any(o['kind']=='remove_members' for o in source['plan'].data['operations']))
        self.assertTrue(any(o['kind']=='add_members' for o in source['plan'].data['operations']))
    def setUp(self): self.s=snapshot();self.m=meta();self.p=adopt_profile(self.s,self.m,'library','#shelf')
    def test_column_remove_only_adopted_book(self):
        m=meta({'calibre-0':[],'calibre-1':['待读'],'calibre-2':[]});v=prepare(self.s,m,self.p)
        self.assertTrue(v['plan'].ready);o=v['plan'].data['operations'];self.assertEqual(len(o),1)
        self.assertEqual(o[0]['kind'],'remove_members');self.assertEqual(o[0]['args']['members'],['b0'])
    def test_missing_book_never_means_remove(self):
        m=meta({'calibre-1':['待读']});v=prepare(self.s,m,self.p)
        self.assertEqual(v['plan'].data['operations'],[])
    def test_multiple_sources_keep_edge(self):
        r=default_rule('tags');self.p['rules']=[r]
        m=meta({'calibre-0':[],'calibre-1':['待读'],'calibre-2':[]});m['rows'][0]['fields']['tags']=['待读']
        v=prepare(self.s,m,self.p)
        self.assertNotIn('remove_members',[o['kind'] for o in v['plan'].data['operations']])
    def test_manual_exclusion_blocks_rule_resurrection(self):
        v=prepare(self.s,self.m,self.p,[intent('remove_members','c1',dict(members=['b0']))])
        j=job(v,self.s);p=apply_effects(self.p,j,j['request']['operations'])
        r=default_rule('tags');p['rules']=[r]
        v2=prepare(self.s,self.m,p)
        self.assertFalse(any('b0' in o['args'].get('members',[]) for o in v2['plan'].data['operations']))
    def test_confirmed_only_persistent_and_idempotent(self):
        m=meta({'calibre-0':['文学'],'calibre-1':['待读'],'calibre-2':[]});v=prepare(self.s,m,self.p);j=job(v,self.s)
        ops=j['request']['operations'];statuses={ops[-1]['op_id']:'not_run'};result=receipt(j['request'],statuses)
        with tempfile.TemporaryDirectory(dir=pathlib.Path(__file__).parent) as d:
            state=StateStore(d);key=state.key('library',self.s['device']);p=state.save(key,self.p,0);state.stage(key,j)
            p,j,c=state.commit_receipt(key,result,apply_effects);self.assertEqual(len(c),len(ops)-1)
            rev=p['revision'];p,j,c=state.commit_receipt(key,result,apply_effects);self.assertEqual(c,[]);self.assertEqual(p['revision'],rev)
            self.assertEqual(StateStore(d).get(key),p)
    def test_direct_rename_backfill_preserves_concurrent_edit(self):
        v=prepare(self.s,self.m,self.p,[intent('rename_collection','c1',dict(name='阅读'))]);j=job(v,self.s)
        m=meta({'calibre-0':['待读','用户新增'],'calibre-1':['待读'],'calibre-2':[]})
        rows=receipt_plan(self.p,j,receipt(j['request']),m)
        self.assertEqual(set(rows[0]['after']),{'阅读','用户新增'})
    def test_regex_ignore_case_preserves_escape_semantics(self):
        self.assertTrue(match('ABC',[r're:\D+'],True));self.assertFalse(match('123',[r're:\D+'],True))
    def test_original_config_real_keys(self):
        r=migrate(dict(Rows={'tags':dict(column='标签',action='',minimum='1',split_char=';',ignore=['^skip'],include=[])},Settings={}))
        self.assertEqual(r['rules'][0]['field'],'tags');self.assertEqual(r['rules'][0]['action'],'none');self.assertEqual(r['rules'][0]['split'],';')
    def test_original_nested_config_and_split_before_include(self):
        doc={'library':{'store':dict(Rows={'tags':dict(action='Create',split_char='|',include=['A'],minimum='1')},Settings={})}}
        r=migrate(doc,'library','store');m=meta({'calibre-0':['A|B']})
        out=evaluate(r['rules'],r['settings'],m,{'calibre-0':['b0']})
        self.assertEqual(next(iter(out['outputs'].values()))['targets'],{'A':['b0']})
    def test_prepare_and_effects_do_not_mutate_original_profile(self):
        original=copy.deepcopy(self.p)
        m=meta({'calibre-0':['文学'],'calibre-1':['待读'],'calibre-2':[]})
        v=prepare(self.s,m,self.p);j=job(v,self.s);apply_effects(self.p,j,j['request']['operations'])
        self.assertEqual(self.p,original)
    def test_renamed_alias_does_not_remove_same_identity(self):
        self.p['bindings']['阅读']='c1'
        m=meta({'calibre-0':['阅读'],'calibre-1':['待读'],'calibre-2':[]})
        v=prepare(self.s,m,self.p)
        self.assertEqual([o['kind'] for o in v['plan'].data['operations']],['verify_state'])
    def test_column_backfill_journal_is_exactly_once(self):
        with tempfile.TemporaryDirectory(dir=pathlib.Path(__file__).parent) as d:
            state=StateStore(d);value=dict(job_id='job',result_digest='result',operations=['op'],rows=[])
            state.stage_column(value);self.assertEqual(state.column_pending('job'),value)
            state.finish_column(value);self.assertIsNone(state.column_pending('job'));self.assertEqual(state.column_done('job'),{'op'})
    def test_multiple_device_copies_respect_explicit_selection(self):
        s=copy.deepcopy(self.s);s['books'].append(dict(s['books'][0],uuid='copy',location='/mnt/us/documents/copy.azw3'))
        s['relations'].append(dict(s['relations'][0],book_uuid='copy'))
        p=adopt_profile(s,self.m,'library','#shelf',{'calibre-0':['b0']})
        m=meta({'calibre-0':[],'calibre-1':['待读'],'calibre-2':[]});v=prepare(s,m,p)
        self.assertEqual(v['plan'].data['operations'][0]['args']['members'],['b0'])
        self.assertTrue(v['warnings'])
    def test_partial_split_filter_preserves_book_scope(self):
        from plugin.rules import DEFAULT_SETTINGS
        r=default_rule('tags');r.update(split=r'\|',ignore=['X'])
        out=evaluate([r],DEFAULT_SETTINGS,meta({'calibre-0':['X|Y']}),{'calibre-0':['b0']})['outputs'][r['id']]
        self.assertEqual(out['targets'],{'Y':['b0']});self.assertEqual(out['filtered'],['calibre-0'])

if __name__=='__main__':
    start=time.perf_counter();r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Workflow))
    pathlib.Path(__file__).with_name('workflow-results.json').write_text(json.dumps(dict(tests=r.testsRun,failures=len(r.failures),errors=len(r.errors),seconds=time.perf_counter()-start)))
    if not r.wasSuccessful():raise SystemExit(1)
