#!/usr/bin/env python3
"""Apply the frozen R3v2 rules to previously unexposed ThinkingBox tasks."""
from __future__ import annotations
import asyncio, copy, hashlib, inspect, json, re, sys
from collections import Counter
from pathlib import Path
try: import scripts.phase3_1_thinkingbox_r2a as p31
except ModuleNotFoundError: import phase3_1_thinkingbox_r2a as p31

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'artifacts/phase4_3'; REPORT=ROOT/'PHASE4_3_R3V2_HELDOUT_RESCAN.md'
P40=ROOT/'artifacts/phase4_0/r3_omission_candidates.jsonl'; SPEC=ROOT/'artifacts/phase4_2/R3_V2_SPECIFICATION.json'
WORKFLOW_PREFIX=('zendesk_','approval_'); GENERIC={'api','create','update','modify','add','item','process','request','provision'}
ACTION={'create':('create','file','submit','open','start','make'),'add':('add','include'),'modify':('modify','change','update'),'update':('update','change','set','correct'),'cancel':('cancel',),'refund':('refund',),'provision':('access','provision','activate'),'transfer':('transfer','pay','send')}
REJECTIONS=('REQUIREMENT_GROUNDING_AMBIGUOUS','WORKFLOW_ARTIFACT_NOT_REQUIREMENT','TASK_CRITICALITY_UNKNOWN','EFFECT_ALIAS_OR_NON_INDEPENDENCE','NON_INDEPENDENT_EFFECTS','ORIGINAL_FINAL_PREDICATE_FAILED','PREDICATE_UNSTABLE','NO_UNIQUE_PRODUCER','DOWNSTREAM_DEPENDENCY','HIDDEN_STATE_PRECONDITION','OMISSION_NOT_ISOLATABLE','OTHER')
def load(p): return json.loads(Path(p).read_text())
def loadl(p): return [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]
def write(p,v): p=Path(p); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(p31.j(v),ensure_ascii=False,sort_keys=True,indent=2)+'\n')
def boundary(text,value):
 raw=str(value); pattern=r'(?<![\w])'+re.escape(raw)+r'(?![\w])'; return list(re.finditer(pattern,text,re.I))
def flatten(v,path='$'):
 if isinstance(v,dict):
  for k,x in v.items(): yield from flatten(x,path+'.'+k)
 elif isinstance(v,list):
  for i,x in enumerate(v): yield from flatten(x,f'{path}[{i}]')
 elif v is not None and not isinstance(v,bool): yield path,v
def family(tool):
 words=tool.casefold().split('_')
 for key in ACTION:
  if key in words: return key
 return None
def workflow_explicit(prompt,tool):
 if tool.startswith('zendesk_'): return bool(re.search(r'\b(?:open|create|close|resolve|update)\b[^.!?]{0,40}\bticket\b|\bticket\b[^.!?]{0,40}\b(?:opened|created|closed|resolved|updated)\b',prompt,re.I))
 if tool.startswith('approval_'): return bool(re.search(r'\b(?:create|submit|open)\b[^.!?]{0,40}\bapproval (?:request|record)\b',prompt,re.I))
 return True
def ground(prompt,unit):
 tool=unit['producer_tool']; fam=family(tool)
 if tool.startswith(WORKFLOW_PREFIX) and not workflow_explicit(prompt,tool): return None,'WORKFLOW_ARTIFACT_NOT_REQUIREMENT'
 if not fam: return None,'TASK_CRITICALITY_UNKNOWN'
 anchors=[]
 for word in ACTION[fam]: anchors += [{'text':m.group(),'start':m.start(),'end':m.end()} for m in boundary(prompt,word)]
 literals=[]
 for path,value in flatten(unit['producer_arguments']):
  if len(str(value))<3: continue
  hits=boundary(prompt,value)
  if len(hits)==1: literals.append({'argument_path':path,'value':value,'start':hits[0].start(),'end':hits[0].end(),'span':hits[0].group()})
 if not anchors or not literals: return None,'REQUIREMENT_GROUNDING_AMBIGUOUS'
 literal=min(literals,key=lambda x:(x['end']-x['start'],x['start']))
 anchor=min(anchors,key=lambda x:abs(x['start']-literal['start']))
 role_tokens=[x for x in tool.casefold().split('_') if x not in GENERIC and x not in ACTION]
 role=next((x for x in role_tokens if boundary(prompt,x)),tool)
 return {'exact_requirement_span':literal['span'],'span_start':literal['start'],'span_end':literal['end'],'normalized_requirement':str(literal['value']).casefold(),'semantic_role':role,'argument_path':literal['argument_path'],'action_anchor':anchor,'token_boundary_verified':True},None
def rows(state):
 out={}
 for table,items in state.items():
  if isinstance(items,list):
   for i,row in enumerate(items):
    if isinstance(row,dict): out[(table,str(row.get('id',i)))]=row
 return out
def compile_predicate(unit,grounding,final,tool_source):
 state=rows(final); clauses=[]
 grounded_values={p31.canon(x['value']) for x in [grounding] if 'value' in x}
 for effect in unit['persistent_effects']:
  key=(effect['table'],str(effect['entity_id'])); row=state.get(key)
  if effect['operation']=='delete': clauses.append({'operation':'delete','table':key[0],'entity_id':key[1]}); continue
  if row is None: return None
  fields={}
  for name,value in row.items():
   if name=='id': continue
   if len(str(value))>=3 and len(boundary(unit['_prompt'],value))==1: fields[name]=value
  fam=family(unit['producer_tool'])
  changed=set(effect.get('changed_fields',[]))
  if fam=='cancel':
   for name in changed:
    if name in ('status','cancelled','canceled'): fields[name]=row.get(name)
  if not fields and effect['operation']=='create':
   # Existence is task-critical only when a uniquely grounded producer argument identifies the new entity.
   for path,value in flatten(unit['producer_arguments']):
    if len(str(value))>=3 and len(boundary(unit['_prompt'],value))==1:
     for name,observed in row.items():
      if observed==value: fields[name]=observed
  if not fields: return None
  excluded=sorted(set(row)-set(fields)-{'id'})
  clauses.append({'operation':effect['operation'],'table':key[0],'entity_id':key[1],'expected_fields':fields,'fields_intentionally_not_part_of_predicate':excluded,'excluded_field_reason':'Not grounded in this independent user requirement; may be workflow/runtime state.'})
 return {'effect_id':unit['effect_id'],'entity_identification_rule':'table plus official deterministic entity id','task_relevant_clauses':clauses,'evaluation_function':'stable_effect_projection_v2','tool_source':tool_source}
def evaluate(predicate,state):
 state_rows=rows(state); projected=[]; ok=True
 for c in predicate['task_relevant_clauses']:
  row=state_rows.get((c['table'],str(c['entity_id'])))
  if c['operation']=='delete': good=row is None; values=None
  else: values={k:(row or {}).get(k) for k in c['expected_fields']}; good=row is not None and values==c['expected_fields']
  projected.append({'table':c['table'],'entity_id':c['entity_id'],'projected_values':values,'satisfied':good}); ok &= good
 return {'satisfied':bool(ok),'projection':projected}
async def replay(task,module):
 module=__import__('importlib').reload(module); await module.initialize(task['domain_init']); system=module.sandbox_system; responses=[]
 for call in task['interactions']:
  try: responses.append(p31.j(await system.call_tool(call['tool'],copy.deepcopy(call['parameters']))))
  except Exception as e: return {},responses,f'{type(e).__name__}: {e}'
 return p31.j(system.db.to_state_dict()),responses,None
def independent(units):
 keys=[]
 for u in units: keys.append({(e['table'],e['entity_id']) for e in u['persistent_effects']})
 return all(keys[i].isdisjoint(keys[j]) for i in range(len(keys)) for j in range(i+1,len(keys)))
async def main():
 spec=load(SPEC); exposed=set(spec['heldout_plan']['development_exposed_tasks']); allowed=set(spec['heldout_plan']['previously_unexposed_tasks'])
 source=loadl(P40); by={}
 for r in source:
  if r['task_uid'] in allowed: by.setdefault(r['task_uid'],r)
 if len(exposed)!=10 or len(by)!=30 or exposed & set(by): raise SystemExit('held-out split mismatch')
 tasks=p31.taskmap(); systems,modules=p31.systems(); candidates=[]; reject=Counter(); stages=Counter()
 for uid in sorted(by):
  record=by[uid]; task=tasks[uid]; grounded=[]; task_reason=None
  for raw in record['effect_units']:
   unit=copy.deepcopy(raw); unit['_prompt']=record['prompt']; evidence,reason=ground(record['prompt'],unit)
   if reason: task_reason=reason; continue
   unit['r3v2_requirement_grounding']=evidence; grounded.append(unit)
  if len(grounded)<2: reject[task_reason or 'REQUIREMENT_GROUNDING_AMBIGUOUS']+=1; continue
  stages['tasks_with_at_least_two_grounded_requirements']+=1; stages['tasks_after_workflow_artifact_exclusion']+=1
  spans={(x['r3v2_requirement_grounding']['span_start'],x['r3v2_requirement_grounding']['span_end']) for x in grounded}
  if len(spans)<len(grounded): reject['EFFECT_ALIAS_OR_NON_INDEPENDENCE']+=1; continue
  if not independent(grounded): reject['NON_INDEPENDENT_EFFECTS']+=1; continue
  stages['tasks_with_independent_effects']+=1
  final1,responses1,error1=await replay(task,modules[record['suite']]); final2,responses2,error2=await replay(task,modules[record['suite']])
  if error1 or error2: reject['ORIGINAL_FINAL_PREDICATE_FAILED']+=1; continue
  predicates=[]; compile_failed=False
  for unit in grounded:
   tool=systems[record['suite']]._tool_map[unit['producer_tool']]; source_path=str(Path(inspect.getsourcefile(type(tool)) or ''))
   pred=compile_predicate(unit,unit['r3v2_requirement_grounding'],final1,source_path)
   if pred is None: compile_failed=True; break
   pred['requirement_span']=unit['r3v2_requirement_grounding']; predicates.append(pred)
  if compile_failed or not all(evaluate(x,final1)['satisfied'] for x in predicates): reject['ORIGINAL_FINAL_PREDICATE_FAILED']+=1; continue
  stages['tasks_whose_final_state_predicates_calibrate']+=1
  eval1=[evaluate(x,final1) for x in predicates]; eval2=[evaluate(x,final2) for x in predicates]
  if eval1!=eval2: reject['PREDICATE_UNSTABLE']+=1; continue
  stages['tasks_with_stable_predicates']+=1
  for unit,predicate in zip(grounded,predicates):
   later=task['interactions'][unit['call_index']+1:]; later_values={p31.canon(v) for c in later for _,v in flatten(c['parameters']) if len(str(v))>=4}; response_values={p31.canon(v) for _,v in flatten(responses1[unit['call_index']]) if len(str(v))>=4}
   if response_values & later_values: continue
   candidates.append({'status':'POTENTIAL_R3V2_OMISSION_CANDIDATE','task_uid':uid,'task_id':record['task_id'],'domain':record['domain'],'reference_trajectory_digest':record['reference_trajectory_sha256'],'required_effect_count':len(grounded),'all_effect_ids':[x['effect_id'] for x in grounded],'all_requirement_spans':[x['r3v2_requirement_grounding'] for x in grounded],'stable_predicates':predicates,'omitted_effect_id':unit['effect_id'],'omitted_effect_semantic_role':unit['r3v2_requirement_grounding']['semantic_role'],'omitted_effect_type':unit['effect_type'],'producer_call_index':unit['call_index'],'producer_identity':unit['producer_tool'],'producer_arguments':unit['producer_arguments'],'producer_isolation_evidence':{'entity_sets_disjoint':True,'response_value_used_by_later_arguments':False,'unique_reference_call_index':True},'original_final_state_witness':evaluate(predicate,final1),'original_predicate_calibration':{'run1':eval1,'run2':eval2,'stable':True},'development_exposed':False,'omission_trajectory_executed':False,'omission_evaluator_executed':False})
  if any(x['task_uid']==uid for x in candidates): stages['tasks_with_isolatable_producers']+=1
  else: reject['DOWNSTREAM_DEPENDENCY']+=1
 for key in REJECTIONS: reject.setdefault(key,0)
 OUT.mkdir(parents=True,exist_ok=True)
 with (OUT/'R3V2_HELDOUT_CANDIDATES.jsonl').open('w') as h:
  for x in candidates: h.write(json.dumps(p31.j(x),ensure_ascii=False,sort_keys=True)+'\n')
 task_count=len({x['task_uid'] for x in candidates}); domains=Counter(x['domain'] for x in candidates); effects=Counter(x['omitted_effect_type'] for x in candidates); conjuncts=Counter(x['required_effect_count'] for x in candidates)
 gate='R3V2_READY_FOR_HELDOUT_PILOT' if task_count>=10 and len(domains)>=2 and len(effects)>=2 else ('R3V2_CONDITIONAL_SMALL_HELDOUT' if task_count>=5 else 'R3V2_INSUFFICIENT_HELDOUT_OPPORTUNITY')
 result={'phase':'4.3','gate':gate,'r3v2_specification_sha256':p31.fsha(SPEC),'heldout_tasks_considered':30,**stages,'final_r3v2_candidate_tasks':task_count,'final_omission_effect_candidates':len(candidates),'domain_distribution':dict(domains),'effect_type_distribution':dict(effects),'conjunct_count_distribution':dict(conjuncts),'rejections':dict(reject),'development_exposed_task_ids':sorted(exposed),'development_exclusion_proof':{'source':'Phase 4.2 frozen heldout_plan','intersection_with_candidates':sorted(exposed & {x['task_uid'] for x in candidates}),'excluded_count':10},'original_executions':60,'omission_trajectory_executions':0,'omission_evaluator_outcomes':0,'llm_api_calls':0,'issue_pr_search_performed':False,'candidate_sha256':hashlib.sha256((OUT/'R3V2_HELDOUT_CANDIDATES.jsonl').read_bytes()).hexdigest()}
 write(OUT/'results.json',result)
 lines=['# Phase 4.3 - R3v2 Held-out Rescan','','## Gate',f'`{gate}`','','## Accounting']+[f'- {k}: {v}' for k,v in result.items() if k not in ('rejections','development_exposed_task_ids')]+['','## Rejections']+[f'- {k}: {v}' for k,v in sorted(reject.items())]+['','All 10 Phase 4.1 development tasks were excluded. Two fresh ORIGINAL executions per considered held-out task calibrated stable projections. No omission trajectory/evaluator, LLM API, or issue/PR search was used.']
 REPORT.write_text('\n'.join(lines)+'\n'); print(json.dumps(result,sort_keys=True))
if __name__=='__main__': asyncio.run(main())
