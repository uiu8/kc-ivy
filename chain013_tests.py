import copy,unittest
from tests import snapshot,receipt
from workflow_tests import meta,job
from plugin.workflow import adopt_profile,prepare,apply_effects
from plugin.planner import intent
from plugin.column_io import receipt_plan,accept_backfill
class Chain(unittest.TestCase):
 def roundtrip(self,ops):
  s=snapshot();m=meta();p=adopt_profile(s,m,'library','#shelf')
  v=prepare(s,m,p,ops);j=job(v,s);r=receipt(j['request']);p=apply_effects(p,j,j['request']['operations'])
  rows=receipt_plan(p,j,r,m);p=accept_backfill(p,rows)
  for row in rows:
   for item in m['rows']:
    if item['uuid']==row['uuid']:item['fields']['#shelf']=row['after']
  for o in j['request']['operations']:
   cid=o['collection_uuid'];kind=o['kind'];args=o['args']
   if kind=='delete_collection':s['collections']=[c for c in s['collections'] if c['uuid']!=cid];s['relations']=[x for x in s['relations'] if x['collection_uuid']!=cid]
   elif kind=='rename_collection':
    for c in s['collections']:
     if c['uuid']==cid:c['name']=args['name']
   elif kind=='create_collection':s['collections'].append(dict(uuid=cid,name=args['name'],complete=True))
   elif kind=='remove_members':s['relations']=[x for x in s['relations'] if not(x['collection_uuid']==cid and x['book_uuid'] in args['members'])]
   elif kind=='add_members':
    for bid in args['members']:s['relations'].append(dict(s['relations'][0],collection_uuid=cid,book_uuid=bid))
  nxt=prepare(s,m,p)
  self.assertFalse(nxt['issues']);self.assertFalse(nxt['plan'].data['blockers']);self.assertFalse(nxt['plan'].data['operations'])
  return s,m,p,j,r
 def test_add_then_remove(self):
  s,m,p,_,_=self.roundtrip([intent('add_members','c2',dict(members=['b0']))])
  self.assertFalse(prepare(s,m,p,[intent('remove_members','c2',dict(members=['b0']))])['issues'])
 def test_remove(self):self.roundtrip([intent('remove_members','c1',dict(members=['b0']))])
 def test_delete(self):self.roundtrip([intent('delete_collection','c1')])
 def test_rename(self):self.roundtrip([intent('rename_collection','c1',dict(name='改名'))])
 def test_create_add(self):self.roundtrip([intent('create_collection','new',dict(name='新架')),intent('add_members','new',dict(members=['b0']))])
 def test_verify_does_not_backfill(self):
  s=snapshot();m=meta();p=adopt_profile(s,m,'library','#shelf');j=job(prepare(s,m,p,[intent('verify_state','c1')]),s)
  self.assertEqual(receipt_plan(p,j,receipt(j['request']),m),[])
 def test_old_deleted_baseline_no_missing(self):
  s,m,p,j,r=self.roundtrip([intent('delete_collection','c1')])
  for b in p['column_baseline'].values():b['values']=['待读']
  self.assertFalse(prepare(s,m,p)['plan'].data['blockers'])
if __name__=='__main__':
 r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Chain));raise SystemExit(not r.wasSuccessful())
