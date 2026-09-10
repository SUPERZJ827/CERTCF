#!/usr/bin/env python3
"""Freeze and run the ThinkingBox R3 omission calibration pilot."""
from __future__ import annotations
import argparse, asyncio, copy, hashlib, importlib.metadata, json, platform, sys
from collections import Counter
from pathlib import Path
try: import scripts.phase3_1_thinkingbox_r2a as p31
except ModuleNotFoundError: import phase3_1_thinkingbox_r2a as p31

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'artifacts/phase4_1'; P40=ROOT/'artifacts/phase4_0'
SOURCE=P40/'r3_omission_candidates.jsonl'; EXPECTED='783944855d84d88ed7f4ba2974e4868d19c3b325f169c48399e49cb7e0ec846f'
SPECS={
 'STATE_CANONICALIZATION_V1.txt':'Phase 3.1 canonical JSON: all fields retained, object keys sorted, list order retained, no normalization.\n',
 'MCP_ADAPTER_V1.txt':'Official golden interactions execute through SandboxToolsSystem.call_tool with identity and parameters unchanged.\n',
 'EFFECT_UNIT_SPECIFICATION_V1.txt':'One effect unit is the complete persistent row-level delta produced by one frozen reference interaction; multiple fields written by that call remain one unit.\n',
 'OMISSION_CERTIFICATE_SPECIFICATION_V1.txt':'Certify only when original satisfies every frozen effect predicate, exactly one producer is deleted, remaining execution succeeds, target predicate is false, every non-target predicate is true, and no equivalent compensation exists.\n',
 'EVALUATOR_RELATION_SPECIFICATION_V1.txt':'Original PASS plus certified omission: omission FAIL is SENSITIVE; omission PASS is R3_FALSE_ACCEPTANCE_CANDIDATE.\n',
}
def load(path): return json.loads(Path(path).read_text())
def loadl(path): return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]
def write(path,value,exclusive=False): p31.write(Path(path),value,exclusive)
def htext(*parts): return hashlib.sha256(''.join(map(str,parts)).encode()).hexdigest()
def uid(record): return record['task_uid']

def make_predicates(record):
 out=[]
 for unit in record['effect_units']:
  clauses=[]
  for effect in unit['persistent_effects']:
   clause={'operation':effect['operation'],'table':effect['table'],'entity_id':effect['entity_id']}
   if effect['operation']=='create': clause['expected_record']=effect['after']
   elif effect['operation']=='update': clause['expected_fields']=effect['after']
   elif effect['operation']=='delete': clause['expected_absent']=True
   clauses.append(clause)
  out.append({'effect_id':unit['effect_id'],'producer_call_index':unit['call_index'],'producer_tool':unit['producer_tool'],'requirement_evidence':unit['requirement_evidence'],'effect_type':unit['effect_type'],'clauses':clauses,'evaluation_function':'persistent_effect_predicate_v1'})
 return out
def state_rows(state):
 out={}
 for table,items in state.items():
  if isinstance(items,list):
   for i,row in enumerate(items):
    if isinstance(row,dict): out[(table,str(row.get('id',i)))]=row
 return out
def eval_predicate(predicate,state):
 rows=state_rows(state); evidence=[]; ok=True
 for c in predicate['clauses']:
  row=rows.get((c['table'],str(c['entity_id'])))
  if c['operation']=='create': good=row==c['expected_record']
  elif c['operation']=='update': good=row is not None and all(row.get(k)==v for k,v in c['expected_fields'].items())
  else: good=row is None
  evidence.append({'clause':c,'observed_record':row,'satisfied':good}); ok &= good
 return {'effect_id':predicate['effect_id'],'satisfied':bool(ok),'clause_evidence':evidence}
def compensation(predicate,state):
 rows=state_rows(state); matches=[]
 for c in predicate['clauses']:
  if c['operation']!='create': continue
  expected={k:v for k,v in c['expected_record'].items() if k!='id'}
  for (table,eid),row in rows.items():
   if table==c['table'] and eid!=str(c['entity_id']) and all(row.get(k)==v for k,v in expected.items()): matches.append({'table':table,'entity_id':eid})
 return matches
def exact(original,omission,index,frozen):
 expected=original[:index]+original[index+1:]
 ok=0<=index<len(original) and original[index]==frozen and omission==expected and len(omission)==len(original)-1
 return {'status':'OMISSION_EXACTNESS_CERTIFIED' if ok else 'OMISSION_CONSTRUCTION_FAILED','deleted_index':index,'deleted_interaction':frozen,'trajectory_length_delta':len(omission)-len(original),'remaining_trajectory_identical':omission==expected}
def select(records):
 grouped={}
 for r in records:
  q=copy.deepcopy(r); q['representative_digest']=htext(r['task_id'],r['omitted_effect_id'],r['producer_call_index'],r['reference_trajectory_sha256'])
  if uid(r) not in grouped or q['representative_digest']<grouped[uid(r)]['representative_digest']: grouped[uid(r)]=q
 reps=sorted(grouped.values(),key=uid)
 for r in reps: r['task_rank_digest']=htext(r['task_id'],r['reference_trajectory_sha256'])
 full=sorted(reps,key=lambda r:(r['task_rank_digest'],uid(r)))
 return reps,full,full[:10]
def freeze():
 receipt=OUT/'FREEZE_RECEIPT.json'
 if receipt.exists(): raise SystemExit('FREEZE_RECEIPT exists; refusing overwrite')
 if p31.fsha(SOURCE)!=EXPECTED: raise SystemExit('Phase 4.0 candidate digest mismatch')
 records=loadl(SOURCE); reps,full,pilot=select(records)
 if len(records)!=71 or len(reps)!=40 or len(pilot)!=10 or len({uid(x) for x in pilot})!=10: raise SystemExit('population/queue mismatch')
 predicates={uid(r):make_predicates(r) for r in reps}
 OUT.mkdir(parents=True,exist_ok=True)
 files={'representatives':OUT/'TASK_REPRESENTATIVES_V1.json','full_queue':OUT/'PILOT_QUEUE_FULL_V1.json','pilot_queue':OUT/'PILOT_QUEUE_V1.json','effect_predicates':OUT/'EFFECT_PREDICATES_V1.json'}
 for name,value in [('representatives',reps),('full_queue',full),('pilot_queue',pilot),('effect_predicates',predicates)]: write(files[name],value)
 for name,text in SPECS.items(): (OUT/name).write_text(text)
 prior=load(ROOT/'artifacts/phase3_2/FREEZE_RECEIPT.json')
 rec={'phase':'4.1','thinkingbox_commit':prior['thinkingbox_commit'],'thinkingbox_data_release':prior['thinkingbox_data_release'],'thinkingbox_data_commit':prior['thinkingbox_data_commit'],'phase4_0_candidate_sha256':p31.fsha(SOURCE),'candidate_effects':71,'candidate_tasks':40,'task_representatives':40,'pilot_tasks':10,'omission_construction_implementation_sha256':p31.fsha(Path(__file__)),'execution_runner_sha256':p31.fsha(Path(__file__)),'mcp_adapter_sha256':p31.fsha(OUT/'MCP_ADAPTER_V1.txt'),'state_canonicalization_sha256':p31.fsha(OUT/'STATE_CANONICALIZATION_V1.txt'),'effect_unit_specification_sha256':p31.fsha(OUT/'EFFECT_UNIT_SPECIFICATION_V1.txt'),'omission_certificate_specification_sha256':p31.fsha(OUT/'OMISSION_CERTIFICATE_SPECIFICATION_V1.txt'),'evaluator_relation_sha256':p31.fsha(OUT/'EVALUATOR_RELATION_SPECIFICATION_V1.txt'),'environment':{'python':sys.version,'platform':platform.platform(),'thinkingbox_package':importlib.metadata.version('thinkingbox'),'mcp_package':importlib.metadata.version('mcp'),'typesense_python':importlib.metadata.version('typesense')},'omission_trajectory_executions_before_freeze':0,'omission_evaluator_outcomes_before_freeze':0,'candidate_selection_manual':False,'pilot_selection_manual':False,'r3_definition_changed':False,'effect_decomposition_changed':False,'evaluator_relation_changed':False,'issue_pr_search_performed':False,'llm_api_calls':0}
 for name,path in files.items(): rec[name+'_sha256']=p31.fsha(path)
 write(receipt,rec,True); print(json.dumps({'frozen':True,'representatives':40,'pilot':10}))

async def execute(task,trajectory,module): return await p31.execute(task,trajectory,-1,module)
def persist(base,traj,execution,initial,final,effects,evaluator):
 for name,value in [('trajectory.json',traj),('execution.json',execution),('initial_state.json',initial),('final_state.json',final),('effects.json',effects),('evaluator.json',evaluator)]: write(base/name,value)
async def case(record,tasks,modules,predicate_map):
 task=tasks[uid(record)]; original=[{'name':x['tool'],'arguments':copy.deepcopy(x['parameters'])} for x in task['interactions']]; index=record['producer_call_index']; omission=original[:index]+original[index+1:]
 cert=exact(original,omission,index,{'name':record['producer_tool'],'arguments':record['producer_arguments']}); predicates=predicate_map[uid(record)]
 om,oe,oi,of,oq,ors,_,_=await execute(task,original,modules[record['suite']]); oval=await p31.evaluate(om,task) if oe['status']=='SUCCESS' else {'verdict':'NOT_RUN','effects':None}
 original_eval=[eval_predicate(x,of) for x in predicates]; original_complete=all(x['satisfied'] for x in original_eval)
 baseline=oe['status']=='SUCCESS' and oval['verdict']=='PASS' and original_complete and cert['status']=='OMISSION_EXACTNESS_CERTIFIED'
 if baseline: mm,me,mi,mf,mq,mrs,_,_=await execute(task,omission,modules[record['suite']])
 else: mm=None; me={'status':'NOT_RUN_BASELINE_FAILED'}; mi=mf={}; mq=mrs=[]
 omitted_eval=[eval_predicate(x,mf) for x in predicates] if me['status']=='SUCCESS' else []
 truth={x['effect_id']:x['satisfied'] for x in omitted_eval}; target=record['omitted_effect_id']; other_ok=bool(truth) and all(value for key,value in truth.items() if key!=target); target_missing=bool(truth) and not truth[target]
 target_pred=next(x for x in predicates if x['effect_id']==target); compensations=compensation(target_pred,mf) if me['status']=='SUCCESS' else []
 checks={'multiple_required_effects':len(predicates)>=2,'original_complete':original_complete,'exact_omission':cert['status']=='OMISSION_EXACTNESS_CERTIFIED','initial_state_equal':baseline and p31.sha(oi)==p31.sha(mi),'remaining_execution_success':me['status']=='SUCCESS','target_effect_missing':target_missing,'all_other_effects_satisfied':other_ok,'no_compensation':not compensations}
 if all(checks.values()): classification='CONJUNCTIVE_OMISSION_CERTIFIED'
 elif me['status']=='ERROR': classification='OMISSION_EXECUTION_INVALID'
 elif me['status'].startswith('NOT_RUN'): classification='CONJUNCTIVE_OMISSION_UNKNOWN'
 elif compensations: classification='COMPENSATED_OMISSION'
 elif target_missing and not other_ok: classification='MULTI_REQUIREMENT_DAMAGE'
 else: classification='CONJUNCTIVE_OMISSION_FAILED'
 if classification=='CONJUNCTIVE_OMISSION_CERTIFIED': mval=await p31.evaluate(mm,task); sensitivity='SENSITIVE' if mval['verdict']=='FAIL' else 'R3_FALSE_ACCEPTANCE_CANDIDATE'
 else: mval={'verdict':'NOT_RUN_CERTIFICATE_NOT_PASSED','effects':None}; sensitivity=None
 cid=f"{record['task_id']}-{hashlib.sha256((target+str(index)).encode()).hexdigest()[:12]}"; base=OUT/'cases'/cid
 metadata={'case_id':cid,'task_uid':uid(record),'task_id':record['task_id'],'domain':record['domain'],'omitted_effect_id':target,'omitted_effect_type':record['omitted_effect_type'],'required_effect_count':len(predicates),'baseline_success':baseline,'original_complete_effect_set_success':original_complete,'sensitivity_classification':sensitivity}
 persist(base/'original',original,oe,oi,of,{'predicate_results':original_eval,'requests':oq,'responses':ors,'official_effects':oval.get('effects')},oval)
 persist(base/'omission',omission,me,mi,mf,{'predicate_results':omitted_eval,'requests':mq,'responses':mrs,'compensation_matches':compensations,'official_effects':mval.get('effects')},mval)
 write(base/'metadata.json',metadata); write(base/'effect_predicates.json',predicates); write(base/'omission_exactness_certificate.json',cert); write(base/'conjunctive_omission_certificate.json',{'classification':classification,'checks':checks,'original_predicates':original_eval,'omission_predicates':omitted_eval,'compensation_matches':compensations})
 return cid
def aggregate():
 rows=[]
 for path in sorted((OUT/'cases').glob('*')):
  rows.append((load(path/'metadata.json'),load(path/'conjunctive_omission_certificate.json'),load(path/'omission_exactness_certificate.json'),load(path/'original/execution.json'),load(path/'omission/execution.json')))
 classes=Counter(x[1]['classification'] for x in rows); sensitivity=Counter(x[0]['sensitivity_classification'] for x in rows if x[0]['sensitivity_classification'])
 counts={'phase4_0_candidate_effects':71,'phase4_0_candidate_tasks':40,'task_representatives':40,'frozen_pilot_tasks':10,'attempted':len(rows),'baseline_replay_success':sum(x[0]['baseline_success'] for x in rows),'original_complete_effect_set_success':sum(x[0]['original_complete_effect_set_success'] for x in rows),'omission_exactness_success':sum(x[2]['status']=='OMISSION_EXACTNESS_CERTIFIED' for x in rows),'omission_execution_success':sum(x[4]['status']=='SUCCESS' for x in rows),'evaluator_comparisons':sum(x[0]['sensitivity_classification'] is not None for x in rows)}
 for key in ('CONJUNCTIVE_OMISSION_CERTIFIED','CONJUNCTIVE_OMISSION_FAILED','CONJUNCTIVE_OMISSION_UNKNOWN','MULTI_REQUIREMENT_DAMAGE','COMPENSATED_OMISSION','OMISSION_EXECUTION_INVALID'): counts[key]=classes[key]
 for key in ('SENSITIVE','R3_FALSE_ACCEPTANCE_CANDIDATE'): counts[key]=sensitivity[key]
 strata={}
 for field in ('domain','omitted_effect_type','required_effect_count'):
  strata[field]={}
  for value in sorted({str(x[0][field]) for x in rows}):
   subset=[x for x in rows if str(x[0][field])==value]; strata[field][value]={'attempted':len(subset),'certified':sum(x[1]['classification']=='CONJUNCTIVE_OMISSION_CERTIFIED' for x in subset),'sensitive':sum(x[0]['sensitivity_classification']=='SENSITIVE' for x in subset),'false_acceptance_candidates':sum(x[0]['sensitivity_classification']=='R3_FALSE_ACCEPTANCE_CANDIDATE' for x in subset)}
 gates=['R3_CALIBRATION_INCONCLUSIVE'] if counts['CONJUNCTIVE_OMISSION_CERTIFIED']<7 else ['R3_MECHANISM_VALIDATED']
 if counts['R3_FALSE_ACCEPTANCE_CANDIDATE']: gates.append('R3_CALIBRATION_SIGNAL_PRESENT')
 elif counts['CONJUNCTIVE_OMISSION_CERTIFIED']>=7 and counts['evaluator_comparisons']==counts['SENSITIVE']: gates.append('R3_EVALUATOR_SENSITIVE_ON_CALIBRATION')
 return {'phase':'4.1','counts':counts,'stratification':strata,'gates':gates,'contamination':{'issue_pr_search_performed':False,'llm_api_calls':0}}
async def run():
 receipt=load(OUT/'FREEZE_RECEIPT.json')
 if receipt['execution_runner_sha256']!=p31.fsha(Path(__file__)): raise SystemExit('runner differs from freeze')
 if (OUT/'cases').exists() and any((OUT/'cases').iterdir()): raise SystemExit('case evidence exists; refusing overwrite')
 pilot=load(OUT/'PILOT_QUEUE_V1.json'); predicates=load(OUT/'EFFECT_PREDICATES_V1.json'); tasks=p31.taskmap(); _,modules=p31.systems()
 for record in pilot: print(await case(record,tasks,modules,predicates),flush=True)
 result=aggregate(); write(OUT/'results.json',result)
 lines=['# Phase 4.1 - R3 Omission Calibration Pilot','','## Gate',*['- `'+x+'`' for x in result['gates']], '', '## Persisted accounting']+[f"- {k}: {v}" for k,v in result['counts'].items()]+['','ThinkingBox remains an R3 calibration target. No issue/PR search or LLM API call was performed.']
 (ROOT/'PHASE4_1_R3_OMISSION_CALIBRATION_PILOT.md').write_text('\n'.join(lines)+'\n'); print(json.dumps(result,sort_keys=True))
if __name__=='__main__':
 parser=argparse.ArgumentParser(); parser.add_argument('command',choices=('freeze','run')); args=parser.parse_args(); freeze() if args.command=='freeze' else asyncio.run(run())
