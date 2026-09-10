#!/usr/bin/env python3
"""Original-only, evaluator-independent ThinkingBox R3 opportunity scan."""
from __future__ import annotations
import asyncio, copy, hashlib, importlib, json, re, sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]; DATA=ROOT/'reference/thinkingbox-data'
OUT=ROOT/'artifacts/phase4_0'; REPORT=ROOT/'PHASE4_0_R3_OMISSION_GATE.md'
sys.path.insert(0,str(ROOT/'scripts'))
import phase3_0_scan_thinkingbox as p30

ACTION_ANCHORS={
 'create':('create','open','file','submit','start','make'), 'add':('add','include','enroll'),
 'send':('send','message','email','notify'), 'transfer':('transfer','send','pay'),
 'update':('update','change','set','modify','correct'), 'modify':('modify','change','update'),
 'delete':('delete','remove','cancel'), 'remove':('remove','delete','cancel'),
 'cancel':('cancel','terminate'), 'schedule':('schedule','book','arrange'), 'book':('book','reserve'),
 'assign':('assign','route'), 'provision':('provision','grant','give access','access'),
 'generate':('generate','create','issue'), 'close':('close','resolve','solve'),
 'refund':('refund','reimburse'), 'change':('change','update','set'),
}
READ_PREFIX=('get','search','list','lookup','retrieve','query','inspect','find','check')

def j(v):
 if hasattr(v,'model_dump'): return j(v.model_dump(mode='json'))
 if isinstance(v,dict): return {str(k):j(x) for k,x in v.items()}
 if isinstance(v,(list,tuple)): return [j(x) for x in v]
 return v if v is None or isinstance(v,(str,int,float,bool)) else str(v)
def canon(v): return json.dumps(j(v),ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha(v): return hashlib.sha256(canon(v).encode()).hexdigest()
def write(path,v):
 path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(j(v),ensure_ascii=False,sort_keys=True,indent=2)+'\n')
def norm(s): return re.sub(r'\s+',' ',str(s).casefold()).strip()
def scalars(v):
 if isinstance(v,dict):
  for x in v.values(): yield from scalars(x)
 elif isinstance(v,list):
  for x in v: yield from scalars(x)
 elif v is not None and not isinstance(v,bool): yield v
def rows(state):
 out={}
 for table,items in state.items():
  if isinstance(items,list):
   for i,row in enumerate(items):
    if isinstance(row,dict): out[(table,str(row.get('id',i)))]=row
 return out
def delta(before,after):
 a,b=rows(before),rows(after); effects=[]
 for key in sorted(set(a)|set(b)):
  if key not in a: effects.append({'operation':'create','table':key[0],'entity_id':key[1],'before':None,'after':b[key]})
  elif key not in b: effects.append({'operation':'delete','table':key[0],'entity_id':key[1],'before':a[key],'after':None})
  elif a[key]!=b[key]:
   changed=sorted(k for k in set(a[key])|set(b[key]) if a[key].get(k)!=b[key].get(k))
   effects.append({'operation':'update','table':key[0],'entity_id':key[1],'changed_fields':changed,'before':{k:a[key].get(k) for k in changed},'after':{k:b[key].get(k) for k in changed}})
 return effects
def action_family(tool):
 words=re.split(r'[_\-]+',tool.casefold())
 if words[0] in READ_PREFIX: return None
 for family in ACTION_ANCHORS:
  if family in words: return family
 return None
def requirement_evidence(prompt,tool,args):
 family=action_family(tool)
 if not family: return None
 text=norm(prompt); anchors=[x for x in ACTION_ANCHORS[family] if x in text]
 literals=[]
 for value in scalars(args):
  token=norm(value)
  if len(token)>=3 and text.count(token)==1: literals.append({'value':value,'normalized':token})
 if not anchors or not literals: return None
 return {'method':'FIXED_ACTION_ANCHOR_PLUS_UNIQUE_EXACT_ARGUMENT_LITERAL','action_family':family,'matched_anchors':anchors,'unique_prompt_literals':literals}
def response_dependency(response,later):
 later_values={canon(x) for call in later for x in scalars(call.get('parameters',{})) if len(str(x))>=4}
 shared=[]
 for value in scalars(response):
  if len(str(value))>=4 and canon(value) in later_values: shared.append(value)
 return sorted({canon(x) for x in shared})

async def replay(task,module):
 module=importlib.reload(module); await module.initialize(task['domain_init']); system=module.sandbox_system
 initial=j(module.initial_db_state); traces=[]; error=None
 for i,call in enumerate(task['interactions']):
  before=j(system.db.to_state_dict())
  try: response=j(await system.call_tool(call['tool'],copy.deepcopy(call['parameters'])))
  except Exception as exc: error=f'{type(exc).__name__}: {exc}'; break
  after=j(system.db.to_state_dict()); traces.append({'call_index':i,'tool':call['tool'],'arguments':call['parameters'],'response':response,'effects':delta(before,after)})
 return initial,j(system.db.to_state_dict()),traces,error

def independent(a,b):
 ka={(x['table'],x['entity_id']) for x in a['persistent_effects']}; kb={(x['table'],x['entity_id']) for x in b['persistent_effects']}
 return ka.isdisjoint(kb)
def candidate_records(task,traces):
 units=[]
 for trace in traces:
  evidence=requirement_evidence(task['prompt'],trace['tool'],trace['arguments'])
  if evidence and trace['effects']:
   units.append({'effect_id':f"{task['spec']}::call-{trace['call_index']}",'call_index':trace['call_index'],'producer_tool':trace['tool'],'producer_arguments':trace['arguments'],'requirement_evidence':evidence,'persistent_effects':trace['effects'],'effect_type':'+'.join(sorted({x['table']+'.'+x['operation'] for x in trace['effects']})),'original_effect_witness':{'status':'REPRODUCED','effects':trace['effects']},'response_digest':sha(trace['response'])})
 if len(units)<2: return [],'SINGLE_EFFECT_TASK'
 eligible=[]
 for target in units:
  others=[x for x in units if x is not target]
  if not all(independent(target,x) for x in others): continue
  dep=response_dependency(traces[target['call_index']]['response'],task['interactions'][target['call_index']+1:])
  if dep: continue
  q={'status':'POTENTIAL_R3_OMISSION_CANDIDATE','task_uid':task['spec'],'task_id':task['task_id'],'suite':task['domain'],'domain':task['domain_label'],'prompt':task['prompt'],'prompt_sha256':sha(task['prompt']),'reference_trajectory_sha256':sha(task['interactions']),'required_effect_count':len(units),'effect_units':units,'omitted_effect_id':target['effect_id'],'omitted_effect_type':target['effect_type'],'producer_call_index':target['call_index'],'producer_tool':target['producer_tool'],'producer_arguments':target['producer_arguments'],'omission_isolatable':True,'downstream_dependency_values':[],'original_only_replay':True,'omission_trajectory_executed':False,'omission_evaluator_executed':False}
  eligible.append(q)
 if eligible: return eligible,None
 touched_overlap=any(not independent(a,b) for i,a in enumerate(units) for b in units[i+1:])
 return [],'OMISSION_NOT_ISOLATABLE' if touched_overlap else 'DOWNSTREAM_DEPENDENCY'

async def main():
 tasks=p30.load_tasks(DATA); _,modules=p30.build_systems(); deterministic=[x for x in tasks if not x['rubric']]
 candidates=[]; rejected=Counter(); original_success=0
 effect_counts=Counter()
 for n,task in enumerate(deterministic,1):
  if not task['interactions']: rejected['REFERENCE_TRAJECTORY_UNCLEAR']+=1; continue
  initial,final,traces,error=await replay(task,modules[task['domain']])
  if error or len(traces)!=len(task['interactions']): rejected['ORIGINAL_EFFECT_WITNESS_FAILED']+=1; continue
  original_success+=1
  records,reason=candidate_records(task,traces)
  if reason: rejected[reason]+=1
  else:
   candidates.extend(records); effect_counts[len(records[0]['effect_units'])]+=1
  if n%50==0: print(f'original-only {n}/{len(deterministic)}',flush=True)
 OUT.mkdir(parents=True,exist_ok=True)
 with (OUT/'r3_omission_candidates.jsonl').open('w') as h:
  for record in candidates: h.write(json.dumps(j(record),ensure_ascii=False,sort_keys=True)+'\n')
 tasks_count=len({x['task_uid'] for x in candidates}); candidate_domains=Counter(x['domain'] for x in candidates); task_domains=Counter({x['task_uid']:x['domain'] for x in candidates}.values()); effects=Counter(x['omitted_effect_type'] for x in candidates)
 for reason in ('SINGLE_EFFECT_TASK','EFFECT_DECOMPOSITION_AMBIGUOUS','NON_INDEPENDENT_EFFECTS','NO_UNIQUE_PRODUCER','OMISSION_NOT_ISOLATABLE','DOWNSTREAM_DEPENDENCY','ORIGINAL_EFFECT_WITNESS_FAILED','REFERENCE_TRAJECTORY_UNCLEAR','OTHER'): rejected.setdefault(reason,0)
 if tasks_count>=10 and len(task_domains)>=2 and len(effects)>=2: gate='R3_READY_FOR_CALIBRATION_PILOT'
 elif tasks_count>=5: gate='R3_CONDITIONAL_SMALL_CALIBRATION'
 else: gate='R3_INSUFFICIENT_OPPORTUNITY'
 result={'phase':'4.0','gate':gate,'total_canonical_tasks':len(tasks),'total_deterministic_tasks':len(deterministic),'original_only_replay_success':original_success,'tasks_with_at_least_two_independently_observable_effects':tasks_count,'tasks_with_unique_isolatable_producer':tasks_count,'potential_omission_effects':len(candidates),'candidate_tasks':tasks_count,'effects_per_task_distribution':dict(effect_counts),'candidate_domain_distribution':dict(candidate_domains),'task_domain_distribution':dict(task_domains),'effect_type_distribution':dict(effects),'rejections':dict(rejected),'omission_trajectory_executions':0,'omission_evaluator_outcomes':0,'llm_api_calls':0,'issue_pr_search_performed':False,'candidate_sha256':hashlib.sha256((OUT/'r3_omission_candidates.jsonl').read_bytes()).hexdigest()}
 write(OUT/'results.json',result)
 lines=['# Phase 4.0 - R3 Conjunctive Requirement Omission Gate','','## Scope','ThinkingBox is an `R3_CALIBRATION_TARGET`. Candidate generation used prompt text, official golden interactions, and ORIGINAL-only state/response evidence; evaluator predicate coverage was not a selection input.','','## Frozen static definition','An effect unit is the complete persistent row-level state delta produced by one reference interaction. Multiple fields written by the same call are one effect unit. A removable producer must touch entities disjoint from every other required-effect producer and its response must not occur in later call arguments.','','## Accounting']+[f'- {k}: {v}' for k,v in result.items() if k not in ('rejections','domain_distribution','effect_type_distribution')]+['','## Rejections']+[f'- {k}: {v}' for k,v in sorted(rejected.items())]+['','## Gate',f'`{gate}`','','No omission trajectory or omission evaluator was executed. No LLM API or issue/PR search was used.']
 REPORT.write_text('\n'.join(lines)+'\n'); print(json.dumps(result,sort_keys=True))
if __name__=='__main__': asyncio.run(main())
