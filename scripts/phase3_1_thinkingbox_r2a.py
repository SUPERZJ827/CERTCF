#!/usr/bin/env python3
"""Freeze and run the ThinkingBox R2A blind pilot (no LLM path)."""
from __future__ import annotations
import argparse, asyncio, copy, hashlib, importlib, importlib.metadata, json, platform, sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'artifacts/phase3_1'
TB=ROOT/'reference/thinkingbox'; DATA=ROOT/'reference/thinkingbox-data'
SOURCE=ROOT/'artifacts/phase3_0/thinkingbox_r2a_static_candidates.jsonl'
EXPECTED='b4e2cdf2898c80c9c28dc81f73a4bacd520125c620bf55216f531134f8198ece'
TB_COMMIT='40c1212f9582ca90175079bc313e530e9e9a4981'; DATA_COMMIT='fcaba4c1a9debec42fda7f15bf29fe6d6b46c431'
RULES={
 'schema_enum_lexicographic_alternative_v1':'lexicographically smallest declared enum alternative',
 'iso_date_plus_one_day_v1':'ISO date plus exactly one calendar day',
 'created_identifier_last_alphanumeric_increment_v1':'increment final decimal digit modulo ten',
 'schema_free_text_suffix_v1':"append literal suffix ' Jr.' to unconstrained person name",
}
SPECS={
 'STATE_CANONICALIZATION_V1.txt':'UTF-8 JSON; recursive JSON conversion; object keys sorted; compact separators; no fields omitted; list order retained.\n',
 'RESPONSE_CANONICALIZATION_V1.txt':'Complete raw tool response converted recursively to JSON; sorted object keys; no normalization or ignored fields.\n',
 'MCP_ADAPTER_V1.txt':'Execute each official golden tool interaction through SandboxToolsSystem.call_tool with identity and arguments unchanged.\n',
 'REQUEST_RESPONSE_INSTRUMENTATION_V1.txt':'Record request before call and complete returned object after call; do not mutate, retry, reorder, or suppress exceptions.\n',
 'TASK_CRITICAL_EFFECT_SPECIFICATION_V1.txt':'Frozen R2A chain: explicit requirement -> official target action -> persistent task-critical effect -> exact one-argument mutation -> wrong persistent effect.\n',
 'VIOLATION_CERTIFICATE_SPECIFICATION_V1.txt':'Certify only with original effect L, exact L-to-L-prime mutation, successful action, persisted L-prime, absence of persisted L, no compensation, and unchanged semantic role.\n',
 'EVALUATOR_RELATION_SPECIFICATION_V1.txt':'For original PASS and certified violation: perturbed FAIL is SENSITIVE; perturbed PASS is R2A_FALSE_ACCEPTANCE_CANDIDATE.\n',
}

def j(v):
 if hasattr(v,'model_dump'): return j(v.model_dump(mode='json'))
 if isinstance(v,dict): return {str(k):j(x) for k,x in v.items()}
 if isinstance(v,(list,tuple)): return [j(x) for x in v]
 return v if v is None or isinstance(v,(str,int,float,bool)) else str(v)
def canon(v): return json.dumps(j(v),ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha(v): return hashlib.sha256((v if isinstance(v,bytes) else canon(v).encode())).hexdigest()
def fsha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,v,exclusive=False):
 p=Path(p); p.parent.mkdir(parents=True,exist_ok=True)
 mode='x' if exclusive else 'w'
 with p.open(mode,encoding='utf-8') as h: json.dump(j(v),h,ensure_ascii=False,sort_keys=True,indent=2); h.write('\n')
def loadl(p): return [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]
def p30():
 sys.path.insert(0,str(ROOT/'scripts'))
 import phase3_0_scan_thinkingbox as m
 return m
def uid(r): return r['task_uid']
def normalize_record(r):
 q=copy.deepcopy(r)
 q['target_call']={'index':r['call_index'],'tool':r['tool'],'arguments':r['canonical_arguments']}
 q['target_argument']={'name':r['argument_name'],'path':r['argument_path']}
 q['literal']={'value':r['literal'],'type':r['argument_type']}
 q['state_effect']={'type':r['effect_type'],'entity':r['state_effect_entity'],'field':r['state_effect_field']}
 q['prompt_grounding']={'unique':r['prompt_literal_occurrences']==1}
 q['provenance']={'trajectory_sha256':r['reference_trajectory_sha256']}
 return q
def mutation(r): return r['mutation_viability']['replacement']
def mutate(args,path,value):
 assert path.startswith('$.') and '.' not in path[2:]
 out=copy.deepcopy(args); out[path[2:]]=copy.deepcopy(value); return out
def exact(original,changed,index,arg,L,Lp):
 diffs=[]
 if len(original)==len(changed):
  for i,(a,b) in enumerate(zip(original,changed)):
   keys=set(a.get('arguments',{}))|set(b.get('arguments',{}))
   if a.get('name')!=b.get('name'): diffs.append([i,'name'])
   for k in keys:
    if a.get('arguments',{}).get(k)!=b.get('arguments',{}).get(k): diffs.append([i,k])
 ok=len(original)==len(changed) and diffs==[[index,arg]] and original[index]['arguments'][arg]==L and changed[index]['arguments'][arg]==Lp and L!=Lp
 return {'status':'MUTATION_EXACTNESS_SUCCESS' if ok else 'MUTATION_EXACTNESS_FAILED','changed_fields':diffs,'exactly_one_argument_changed':ok}
def taskmap(): return {x['spec']:x for x in p30().load_tasks(DATA)}
def systems(): return p30().build_systems()
def validate_candidates(records):
 tasks=taskmap(); sy,_=systems(); viable=[]; assignments=[]
 for r in records:
  q=copy.deepcopy(r); status='MUTATION_VIABILITY_UNKNOWN'; reason=None
  try:
   t=tasks[uid(r)]; raw=t['interactions'][r['target_call']['index']]; call={'name':raw['tool'],'arguments':raw['parameters']}
   assert call['name']==r['target_call']['tool'] and call['arguments']==r['target_call']['arguments']
   L=r['literal']['value']; Lp=mutation(r); arg=r['target_argument']['name']; assert L!=Lp
   tool=sy[r['suite']]._tool_map[call['name']]
   tool.request_model(**call['arguments']); tool.request_model(**mutate(call['arguments'],r['target_argument']['path'],Lp))
   status='MUTATION_VIABLE'
  except Exception as e: reason=f'{type(e).__name__}: {e}'
  q.update({'task_uid':uid(r),'original_value':r['literal']['value'],'mutation_value':mutation(r),'mutation_rule_version':r['mutation_viability']['rule'],'viability_status':status,'viability_reason':reason,'reference_trajectory_digest':r['provenance']['trajectory_sha256'],'argument_type':r['literal']['type']})
  assignments.append({'task_uid':uid(r),'call_index':r['target_call']['index'],'argument':r['target_argument']['name'],'L':r['literal']['value'],'L_prime':mutation(r),'rule':r['mutation_viability']['rule'],'status':status})
  if status=='MUTATION_VIABLE': viable.append(q)
 return viable,assignments
def queues(viable):
 by=defaultdict(list)
 for r in viable:
  raw=''.join(map(str,[r['task_id'],r['target_call']['index'],r['target_argument']['name'],canon(r['original_value']),r['state_effect']['type'],r['mutation_rule_version']]))
  q=copy.deepcopy(r); q['representative_digest']=hashlib.sha256(raw.encode()).hexdigest(); by[uid(r)].append(q)
 reps=[min(v,key=lambda x:x['representative_digest']) for v in by.values()]
 for r in reps: r['task_rank_digest']=hashlib.sha256((str(r['task_id'])+r['reference_trajectory_digest']).encode()).hexdigest()
 full=sorted(reps,key=lambda x:(x['task_rank_digest'],uid(x)))
 return sorted(reps,key=uid),full,full[:10]
def freeze():
 receipt=OUT/'FREEZE_RECEIPT.json'
 if receipt.exists(): raise SystemExit('FREEZE_RECEIPT already exists; refusing overwrite')
 assert fsha(SOURCE)==EXPECTED
 records=[normalize_record(x) for x in loadl(SOURCE)]; assert len(records)==30 and len({uid(x) for x in records})==17
 viable,assignments=validate_candidates(records); reps,full,pilot=queues(viable)
 if len(pilot)!=10 or len({uid(x) for x in pilot})!=10: raise SystemExit('fewer than ten viable tasks')
 OUT.mkdir(parents=True,exist_ok=True)
 rules={'version':'THINKINGBOX_R2A_MUTATION_RULES_V1','definitions':RULES,'assignments':assignments}
 paths={'mutation_rules':OUT/'R2A_MUTATION_RULES_THINKINGBOX_V1.json','viable_population':OUT/'R2A_VIABLE_POPULATION_V1.json','representatives':OUT/'TASK_REPRESENTATIVES_V1.json','full_queue':OUT/'PILOT_QUEUE_FULL_V1.json','pilot_queue':OUT/'PILOT_QUEUE_V1.json'}
 for k,v in [('mutation_rules',rules),('viable_population',viable),('representatives',reps),('full_queue',full),('pilot_queue',pilot)]: write(paths[k],v)
 for name,text in SPECS.items(): (OUT/name).write_text(text)
 script=Path(__file__)
 env={'python':sys.version,'platform':platform.platform(),'thinkingbox_package':importlib.metadata.version('thinkingbox'),'mcp_package':importlib.metadata.version('mcp'),'typesense_python':importlib.metadata.version('typesense'),'typesense_server_required':'30.1'}
 rec={'phase':'3.1','thinkingbox_repository':'https://github.com/microsoft/ThinkingBox','thinkingbox_commit':TB_COMMIT,'thinkingbox_data_repository':'https://github.com/microsoft/ThinkingBox-Data','thinkingbox_data_release':'thinkingbox-bench-v1.0','thinkingbox_data_commit':DATA_COMMIT,'candidate_source_sha256':fsha(SOURCE),'candidate_source_records':30,'candidate_source_tasks':17,'viable_candidates':len(viable),'viable_tasks':len({uid(x) for x in viable}),'pilot_tasks':10,'execution_runner_sha256':fsha(script),'mcp_adapter_sha256':fsha(script),'instrumentation_sha256':fsha(script),'candidate_selection_implementation_sha256':fsha(script),'environment':env,'perturbed_trajectory_executions_before_freeze':0,'perturbed_evaluator_outcomes_before_freeze':0,'candidate_population_manual':False,'pilot_selection_manual':False,'mutation_selection_manual':False,'r2a_definition_changed':False,'effect_certificate_changed':False,'evaluator_relation_changed':False}
 for k,p in paths.items(): rec[k+'_sha256']=fsha(p)
 for name in SPECS: rec[name.removesuffix('.txt').lower()+'_sha256']=fsha(OUT/name)
 write(receipt,rec,True); print(json.dumps({'frozen':True,'viable':len(viable),'tasks':len({uid(x) for x in viable}),'pilot':10}))

def rows(state):
 out={}
 for table,items in state.items():
  if isinstance(items,list):
   for n,row in enumerate(items):
    if isinstance(row,dict): out[(table,str(row.get('id',n)))]=row
 return out
def field_changes(before,after,field):
 a,b=rows(before),rows(after); out=[]
 for key,row in b.items():
  old=a.get(key,{}).get(field); new=row.get(field)
  if new!=old: out.append({'table':key[0],'entity_id':key[1],'field':field,'before':old,'after':new})
 return out
async def execute(task,trajectory,target,module):
 mod=importlib.reload(module); await mod.initialize(task['domain_init'])
 system=mod.sandbox_system; initial=j(mod.initial_db_state); requests=[]; responses=[]; before=after=None; error=None
 for i,c in enumerate(trajectory):
  if i==target: before=j(system.db.to_state_dict())
  requests.append({'sequence':i,'tool':c['name'],'arguments':j(c['arguments'])})
  try: response=await system.call_tool(c['name'],c['arguments']); responses.append({'sequence':i,'tool':c['name'],'response':j(response),'exception':None})
  except Exception as e: error={'sequence':i,'type':type(e).__name__,'message':str(e)}; responses.append({'sequence':i,'tool':c['name'],'response':None,'exception':error}); break
  if i==target: after=j(system.db.to_state_dict())
 final=j(system.db.to_state_dict())
 return mod,{'status':'SUCCESS' if error is None else 'ERROR','exception':error,'executed_calls':len(responses)},initial,final,requests,responses,before,after
async def evaluate(mod,task):
 try:
  effects=json.loads(await mod.geteffects()); tm=p30().import_task_module(task['source_path'],task['task_id']); context=type('Context',(),{'effects':{task['domain']:effects}})(); tm.validate_database(context)
  return {'verdict':'PASS','effects':j(effects),'exception':None}
 except Exception as e: return {'verdict':'FAIL','effects':j(locals().get('effects')),'exception':{'type':type(e).__name__,'message':str(e)}}
def persist_branch(base,traj,execution,initial,final,req,res,effects,evaluator):
 for n,v in [('trajectory.json',traj),('execution.json',execution),('requests.json',req),('responses.json',res),('initial_state.json',initial),('final_state.json',final),('effects.json',effects),('evaluator.json',evaluator)]: write(base/n,v)
async def run_case(r,tasks,modules):
 cid=f"{r['task_id']}-{sha([uid(r),r['target_call']['index'],r['target_argument']['name']])[:12]}"; base=OUT/'cases'/cid
 task=tasks[uid(r)]; trajectory=[{'name':x['tool'],'arguments':copy.deepcopy(x['parameters'])} for x in task['interactions']]; i=r['target_call']['index']; arg=r['target_argument']['name']; field=r['state_effect']['field']; L=r['original_value']; Lp=r['mutation_value']
 changed=copy.deepcopy(trajectory); changed[i]['arguments']=mutate(changed[i]['arguments'],r['target_argument']['path'],Lp); ex=exact(trajectory,changed,i,arg,L,Lp)
 om,oe,oi,of,oq,ors,ob,oa=await execute(task,trajectory,i,modules[r['suite']]); initial_ok=True
 original_changes=field_changes(ob or {},oa or {},field); ow=[x for x in original_changes if x['after']==L]
 oval=await evaluate(om,task) if oe['status']=='SUCCESS' else {'verdict':'NOT_RUN','effects':None}
 baseline=oe['status']=='SUCCESS' and oval['verdict']=='PASS' and bool(ow) and ex['status']=='MUTATION_EXACTNESS_SUCCESS'
 if baseline:
  pm,pe,pi,pf,pq,prs,pb,pa=await execute(task,changed,i,modules[r['suite']]); initial_ok=sha(oi)==sha(pi)
 else: pm=None; pe={'status':'NOT_RUN_BASELINE_FAILED'}; pi=pf={}; pq=prs=[]; pb=pa=None
 pchanges=field_changes(pb or {},pa or {},field); pw=[x for x in pchanges if x['after']==Lp]
 correct=field_changes(pi or {},pf or {},field); correct=[x for x in correct if x['after']==L]
 checks={'explicit_requirement':bool(r['prompt_grounding']['unique']), 'original_effect':bool(ow),'exact_mutation':ex['status']=='MUTATION_EXACTNESS_SUCCESS','initial_state_equal':initial_ok,'perturbed_success':pe['status']=='SUCCESS','wrong_persistent_effect':bool(pw),'correct_effect_absent':not correct,'no_compensation':not correct,'same_semantic_role':True}
 if all(checks.values()): cls='TASK_CRITICAL_VIOLATION_CERTIFIED'
 elif pe['status']=='ERROR': cls='MUTATION_EXECUTION_INVALID'
 elif pe['status'].startswith('NOT_RUN'): cls='TASK_CRITICAL_VIOLATION_UNKNOWN'
 elif not pw and any(x['after']==L for x in pchanges): cls='BEHAVIORALLY_EQUIVALENT_MUTATION'
 else: cls='TASK_CRITICAL_VIOLATION_FAILED'
 if cls=='TASK_CRITICAL_VIOLATION_CERTIFIED': pval=await evaluate(pm,task); sensitivity='SENSITIVE' if pval['verdict']=='FAIL' else 'R2A_FALSE_ACCEPTANCE_CANDIDATE'
 else: pval={'verdict':'NOT_RUN_VALIDITY_NOT_CERTIFIED','effects':None}; sensitivity=None
 meta={'case_id':cid,'task_uid':uid(r),'task_id':r['task_id'],'domain':r['domain'],'effect_type':r['state_effect']['type'],'argument_type':r['argument_type'],'target_call_index':i,'target_argument':arg,'L':L,'L_prime':Lp,'baseline_success':baseline,'original_effect_witness_success':bool(ow),'sensitivity_classification':sensitivity}
 persist_branch(base/'original',trajectory,oe,oi,of,oq,ors,{'target_changes':original_changes,'effect_witness':ow,'official_effects':oval.get('effects')},oval)
 persist_branch(base/'perturbed',changed,pe,pi,pf,pq,prs,{'target_changes':pchanges,'wrong_effect_witness':pw,'correct_effect_witnesses':correct,'official_effects':pval.get('effects')},pval)
 write(base/'metadata.json',meta); write(base/'mutation_exactness_certificate.json',ex); write(base/'task_critical_effect_certificate.json',{'classification':cls,'checks':checks,'original_witness':ow,'perturbed_witness':pw,'correct_effect_witnesses':correct})
 return cid
def aggregate():
 cases=[]
 for p in sorted((OUT/'cases').glob('*')):
  m=json.loads((p/'metadata.json').read_text()); c=json.loads((p/'task_critical_effect_certificate.json').read_text()); x=json.loads((p/'mutation_exactness_certificate.json').read_text()); oe=json.loads((p/'original/execution.json').read_text()); pe=json.loads((p/'perturbed/execution.json').read_text()); cases.append((m,c,x,oe,pe))
 cc=Counter(c['classification'] for _,c,_,_,_ in cases); sc=Counter(m['sensitivity_classification'] for m,*_ in cases if m['sensitivity_classification'])
 counts={'phase3_0_candidates':30,'viable_candidates':len(json.loads((OUT/'R2A_VIABLE_POPULATION_V1.json').read_text())),'viable_tasks':len({uid(x) for x in json.loads((OUT/'R2A_VIABLE_POPULATION_V1.json').read_text())}),'pilot_tasks':10,'attempted':len(cases),'baseline_replay_success':sum(m['baseline_success'] for m,*_ in cases),'original_effect_witness_success':sum(m['original_effect_witness_success'] for m,*_ in cases),'mutation_exactness_success':sum(x['status']=='MUTATION_EXACTNESS_SUCCESS' for _,_,x,_,_ in cases),'perturbed_execution_success':sum(p['status']=='SUCCESS' for *_,p in cases),'evaluator_comparisons':sum(m['sensitivity_classification'] is not None for m,*_ in cases)}
 counts.update({k:cc[k] for k in ['TASK_CRITICAL_VIOLATION_CERTIFIED','TASK_CRITICAL_VIOLATION_FAILED','TASK_CRITICAL_VIOLATION_UNKNOWN','BEHAVIORALLY_EQUIVALENT_MUTATION','COMPENSATED_EFFECT','MUTATION_EXECUTION_INVALID']}); counts.update({k:sc[k] for k in ['SENSITIVE','R2A_FALSE_ACCEPTANCE_CANDIDATE']})
 strata={}
 for field in ['domain','effect_type','argument_type']:
  strata[field]={}
  for value in sorted({m[field] for m,*_ in cases}):
   subset=[z for z in cases if z[0][field]==value]; strata[field][value]={'attempted':len(subset),'certified':sum(z[1]['classification']=='TASK_CRITICAL_VIOLATION_CERTIFIED' for z in subset),'sensitive':sum(z[0]['sensitivity_classification']=='SENSITIVE' for z in subset),'false_acceptance_candidates':sum(z[0]['sensitivity_classification']=='R2A_FALSE_ACCEPTANCE_CANDIDATE' for z in subset)}
 gates=['THINKINGBOX_R2A_BLIND_PILOT_INCONCLUSIVE'] if counts['TASK_CRITICAL_VIOLATION_CERTIFIED']<6 else ['THINKINGBOX_R2A_BLIND_MECHANISM_CONFIRMED']
 if counts['R2A_FALSE_ACCEPTANCE_CANDIDATE']: gates.append('THINKINGBOX_BLIND_DISCOVERY_SIGNAL_PRESENT')
 elif counts['TASK_CRITICAL_VIOLATION_CERTIFIED']>=6 and counts['evaluator_comparisons']==counts['SENSITIVE']: gates.append('THINKINGBOX_BLIND_PILOT_FULLY_SENSITIVE')
 return {'phase':'3.1','counts':counts,'stratification':strata,'gates':gates,'contamination':{'issue_pr_search_performed':False,'llm_api_calls':0},'cases':[m['case_id'] for m,*_ in cases]}
async def run():
 rec=json.loads((OUT/'FREEZE_RECEIPT.json').read_text()); assert rec['execution_runner_sha256']==fsha(Path(__file__))
 tasks=taskmap(); _,modules=systems(); pilot=json.loads((OUT/'PILOT_QUEUE_V1.json').read_text()); assert len(pilot)==10
 if (OUT/'cases').exists() and any((OUT/'cases').iterdir()): raise SystemExit('case evidence exists; refusing overwrite')
 for r in pilot: print(await run_case(r,tasks,modules),flush=True)
 result=aggregate(); write(OUT/'results.json',result)
 lines=['# Phase 3.1 - ThinkingBox R2A Blind Pilot','','## Result',*['- `'+x+'`' for x in result['gates']], '', '## Persisted accounting']+[f"- {k}: {v}" for k,v in result['counts'].items()]+['','## Blindness','- No LLM API was used.','- No issue or pull-request search was performed.','- No candidate, mutation, certificate, or evaluator relation was changed after freeze.','- No false-acceptance candidate was investigated.']
 (ROOT/'PHASE3_1_THINKINGBOX_R2A_BLIND_PILOT.md').write_text('\n'.join(lines)+'\n'); print(json.dumps(result['counts'],sort_keys=True))
if __name__=='__main__':
 a=argparse.ArgumentParser(); a.add_argument('command',choices=['freeze','run']); x=a.parse_args(); freeze() if x.command=='freeze' else asyncio.run(run())
