#!/usr/bin/env python3
"""Frozen AppWorld R3v2 conditional small calibration."""
from __future__ import annotations
from os import environ
import argparse, ast, copy, hashlib, importlib.util, json, os, platform, sqlite3, sys
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; DATA=Path(environ.get("CERTCF_APPWORLD_ROOT", str(Path(__file__).resolve().parents[1] / "data/appworld"))); OUT=ROOT/'artifacts/phase4_6'
CAND=ROOT/'artifacts/phase4_5/appworld_r3v2_candidates.jsonl'; SPEC=ROOT/'artifacts/phase4_2/R3_V2_SPECIFICATION.json'
os.environ['APPWORLD_ROOT']=str(DATA)
sp=importlib.util.spec_from_file_location('p46obs',ROOT/'scripts/phase0_9_observability_calibration.py'); assert sp and sp.loader
OBS=importlib.util.module_from_spec(sp); sys.modules[sp.name]=OBS; sp.loader.exec_module(OBS); P7=OBS.P7
def canonical(v): return P7.canonical(v)
def dump(v): return json.dumps(canonical(v),ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha_bytes(v): return hashlib.sha256(v).hexdigest()
def sha_obj(v): return sha_bytes(dump(v).encode())
def fsha(p): return sha_bytes(Path(p).read_bytes())
def write(p,v): p=Path(p); p.parent.mkdir(parents=True,exist_ok=True); P7.write_json(p,v)
def chain(node):
 parts=[]
 while isinstance(node,ast.Attribute): parts.append(node.attr); node=node.value
 if isinstance(node,ast.Name): parts.append(node.id)
 return list(reversed(parts))
def identity(stmt):
 if not isinstance(stmt,ast.Expr) or not isinstance(stmt.value,ast.Call): return None
 p=chain(stmt.value.func)
 return '.'.join(p[1:]) if len(p)==3 and p[0]=='apis' else None
def statement_lists(node):
 for _,v in ast.iter_fields(node):
  if isinstance(v,list) and all(isinstance(x,ast.stmt) for x in v): yield v
  if isinstance(v,ast.AST): yield from statement_lists(v)
  elif isinstance(v,list):
   for x in v:
    if isinstance(x,ast.AST): yield from statement_lists(x)
def transform(source,target):
 tree=ast.parse(source); matches=[]
 for body in statement_lists(tree):
  for i,stmt in enumerate(body):
   if identity(stmt)==target: matches.append((body,i,stmt))
 if len(matches)!=1: return None,{'classification':'OMISSION_CONSTRUCTION_FAILED','reason':'target_direct_statement_match_count','matches':len(matches)}
 body,i,removed=matches[0]; removed_dump=ast.dump(removed,include_attributes=False); del body[i]; ast.fix_missing_locations(tree)
 transformed=ast.unparse(tree)+'\n'
 after=ast.parse(transformed)
 before_calls=[identity(s) for b in statement_lists(ast.parse(source)) for s in b if identity(s)]
 after_calls=[identity(s) for b in statement_lists(after) for s in b if identity(s)]
 expected=before_calls.copy(); expected.remove(target)
 cert={'classification':'OMISSION_EXACTNESS_CERTIFIED','trajectory_length_delta':-1,'removed_identity':target,'removed_statement_ast':removed_dump,'remaining_call_sequence_equal':after_calls==expected,'no_added_or_modified_calls':after_calls==expected,'source_sha256':sha_bytes(source.encode()),'transformed_sha256':sha_bytes(transformed.encode())}
 if not all((cert['remaining_call_sequence_equal'],cert['no_added_or_modified_calls'])): cert['classification']='OMISSION_CONSTRUCTION_FAILED'
 return transformed,cert
def predicate(dbdir,p):
 app,table=p['model'].split('.',1); db=sqlite3.connect(Path(dbdir)/(app+'.db')); db.row_factory=sqlite3.Row
 try:
  cols=[dict(x) for x in db.execute(f'pragma table_info("{table}")')]; pk=[x['name'] for x in cols if x['pk']]
  if len(pk)!=1: return {'result':None,'reason':'non_single_primary_key'}
  row=db.execute(f'select * from "{table}" where "{pk[0]}"=?',(p['record_id'],)).fetchone()
  if p.get('expected_absent'): return {'result':row is None,'observed':None if row is None else canonical(dict(row))}
  observed=None if row is None else canonical(dict(row)); expected=p.get('expected_fields',{}); ok=row is not None and all(row[k]==v for k,v in expected.items())
  return {'result':ok,'expected':expected,'observed':None if observed is None else {k:observed.get(k) for k in expected}}
 finally: db.close()
def run(task_id,source,branch,evaluate,predicates):
 from appworld.environment import AppWorld
 d=OUT/'cases'/task_id/branch; d.mkdir(parents=True,exist_ok=True); world=AppWorld(task_id=task_id,experiment_name=f'phase4_6_{task_id}_{branch}',ground_truth_mode='full'); trace=[]
 try:
  world.models.save(str(d/'initial_state'),format='full',delete_if_exists=True); initial,_=P7.file_tree_digest(d/'initial_state')
  raw=world.requester.request
  def traced(*args,**kwargs):
   app=kwargs.get('_app_name',args[0] if args else None); api=kwargs.get('_api_name',args[1] if len(args)>1 else None); data={k:v for k,v in kwargs.items() if k not in {'_app_name','_api_name','client','raise_on_failure','show','track'}}
   try: r=raw(*args,**kwargs); trace.append({'sequence':len(trace),'api_identity':f'{app}.{api}','arguments':canonical(data),'response':canonical(r)}); return r
   except Exception as e: trace.append({'sequence':len(trace),'api_identity':f'{app}.{api}','arguments':canonical(data),'exception':{'type':type(e).__name__,'message':str(e)}}); raise
  world.requester.request=traced; msg=world.execute(source+'\n__phase46_return=solution(apis, requester)\n'); success=P7.execution_ok(msg)
  world.models.save(str(d/'final_state'),format='full',delete_if_exists=True); final,_=P7.file_tree_digest(d/'final_state')
  effects=[predicate(d/'final_state',p) for p in predicates]
  should_evaluate=evaluate(effects,success) if callable(evaluate) else evaluate
  evaluator=P7.evaluator_payload(world) if success and should_evaluate else {'status':'NOT_RUN'}
  payload={'execution_success':success,'message':msg,'initial_state_digest':initial,'final_state_digest':final,'api_trace':trace,'effect_predicates':effects,'evaluator':evaluator}
  (d/'trajectory.py').write_text(source); write(d/'execution.json',payload); write(d/'effects.json',effects); write(d/'evaluator.json',evaluator); return payload
 finally: world.close()
def population():
 rows=[json.loads(x) for x in CAND.read_text().splitlines() if x.strip()]; by={}
 for r in rows:
  key=r['task_id']; digest=sha_obj([key,r['omitted_effect_id'],r['producer_trace_index'],r['producer_api_identity']]); r['selection_digest']=digest
  if key not in by or digest<by[key]['selection_digest']: by[key]=r
 return [by[k] for k in sorted(by,key=lambda k:sha_obj([k,by[k]['selection_digest']]))]
def freeze():
 assert fsha(CAND)=='496d2993ff47504eb6f97e7790d93b896bef14f172c870946a290afead572f10'
 pop=population(); assert len(pop)==6
 write(OUT/'PILOT_QUEUE_V1.json',pop)
 specs={'effect_predicate':'frozen stable final-state projections from Phase 4.5','certificate':'all original predicates true; exactly target false after successful exact omission; all non-target predicates true; no compensation','relation':'original PASS and certified omission expected FAIL'}
 receipt={'phase':'4.6','status':'FROZEN_PRE_EXECUTION','parent_gate':'APPWORLD_R3V2_CONDITIONAL_SMALL_CALIBRATION','candidate_sha256':fsha(CAND),'r3v2_specification_sha256':fsha(SPEC),'queue_sha256':fsha(OUT/'PILOT_QUEUE_V1.json'),'runner_sha256':fsha(__file__),'specifications':specs,'specification_sha256':sha_obj(specs),'appworld':{'commit':'42b5bcf3cd334fee33f0c37d02070a9f5807add5','package':'0.2.0.dev0','data':'0.2.0'},'environment':{'python':sys.version,'platform':platform.platform()},'candidate_selection_manual':False,'omission_selection_manual':False,'r3v2_core_changed':False,'omission_trajectory_executions_before_freeze':0,'omission_evaluator_outcomes_before_freeze':0,'llm_api_calls':0,'issue_pr_search_performed':False}
 assert not (OUT/'FREEZE_RECEIPT.json').exists(); write(OUT/'FREEZE_RECEIPT.json',receipt); print(dump(receipt))
def execute():
 assert (OUT/'FREEZE_RECEIPT.json').exists(); queue=json.loads((OUT/'PILOT_QUEUE_V1.json').read_text())
 for n,c in enumerate(queue,1):
  tid=c['task_id']; case=OUT/'cases'/tid; case.mkdir(parents=True,exist_ok=True); source=(DATA/'data/tasks'/tid/'ground_truth/compiled_solution.py').read_text(); transformed,exact=transform(source,c['producer_api_identity']); write(case/'metadata.json',c); write(case/'effect_predicates.json',c['all_effect_predicates']); write(case/'omission_exactness_certificate.json',exact)
  if transformed is None: write(case/'conjunctive_omission_certificate.json',{'classification':'CONJUNCTIVE_OMISSION_UNKNOWN','reason':'OMISSION_CONSTRUCTION_FAILED'}); continue
  original=run(tid,source,'original',True,c['all_effect_predicates']); baseline=original['execution_success'] and original['evaluator'].get('status')=='COMPLETED' and original['evaluator']['result'].get('success') and all(x['result'] is True for x in original['effect_predicates'])
  if not baseline: write(case/'conjunctive_omission_certificate.json',{'classification':'CONJUNCTIVE_OMISSION_UNKNOWN','reason':'BASELINE_EFFECT_SET_REPLAY_FAILED'}); continue
  target=[i for i,p in enumerate(c['all_effect_predicates']) if p['model']==c['original_effect_witness']['model'] and p['record_id']==c['original_effect_witness']['record_id']][0]
  omission=run(tid,transformed,'omission',lambda effects,ok: ok and effects[target]['result'] is False and all(x['result'] is True for i,x in enumerate(effects) if i!=target),c['all_effect_predicates'])
  vals=[x['result'] for x in omission['effect_predicates']]; certified=omission['execution_success'] and vals[target] is False and all(v is True for i,v in enumerate(vals) if i!=target)
  classification='CONJUNCTIVE_OMISSION_CERTIFIED' if certified else ('OMISSION_EXECUTION_INVALID' if not omission['execution_success'] else 'CONJUNCTIVE_OMISSION_FAILED')
  cert={'classification':classification,'original_all_effects':True,'omission_target_effect':vals[target],'omission_non_target_effects':[v for i,v in enumerate(vals) if i!=target],'no_compensation':vals[target] is False,'execution_success':omission['execution_success'],'initial_state_equal':original['initial_state_digest']==omission['initial_state_digest']}
  if certified and cert['initial_state_equal']:
   verdict=omission['evaluator']['result'].get('success'); cert['evaluator_classification']='R3_FALSE_ACCEPTANCE_CANDIDATE' if verdict else 'SENSITIVE'
  elif certified: cert={'classification':'CONJUNCTIVE_OMISSION_UNKNOWN','reason':'INITIAL_STATE_MISMATCH',**cert}
  write(case/'conjunctive_omission_certificate.json',cert); print(f'{n}/6 {tid} {cert["classification"]} {cert.get("evaluator_classification","")}',flush=True)
 aggregate()
def aggregate():
 certs=[]
 for p in (OUT/'cases').glob('*/conjunctive_omission_certificate.json'): certs.append(json.loads(p.read_text()))
 count=Counter(x['classification'] for x in certs); ev=Counter(x.get('evaluator_classification') for x in certs if x.get('evaluator_classification'))
 result={'phase':'4.6','population_tasks':6,'attempted':len(certs),'conjunctive_omission_certified':count['CONJUNCTIVE_OMISSION_CERTIFIED'],'unknown':count['CONJUNCTIVE_OMISSION_UNKNOWN'],'failed':count['CONJUNCTIVE_OMISSION_FAILED'],'execution_invalid':count['OMISSION_EXECUTION_INVALID'],'evaluator_comparisons':sum(ev.values()),'sensitive':ev['SENSITIVE'],'r3_false_acceptance_candidates':ev['R3_FALSE_ACCEPTANCE_CANDIDATE']}
 result['gate']='R3V2_CROSS_BENCHMARK_SMALL_CALIBRATION_SUPPORTED' if result['conjunctive_omission_certified']>=5 else 'R3V2_SMALL_CALIBRATION_INCONCLUSIVE'
 write(OUT/'results.json',result); print(dump(result))
if __name__=='__main__':
 a=argparse.ArgumentParser(); a.add_argument('mode',choices=('freeze','execute')); ns=a.parse_args(); freeze() if ns.mode=='freeze' else execute()
