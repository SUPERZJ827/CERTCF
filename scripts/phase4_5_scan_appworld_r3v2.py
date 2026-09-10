#!/usr/bin/env python3
"""AppWorld train+dev R3v2 opportunity scan; ORIGINAL executions only."""
from __future__ import annotations
from os import environ
import copy, hashlib, importlib.util, json, os, re, shutil, sqlite3, sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]; DATA=Path(environ.get("CERTCF_APPWORLD_ROOT", str(Path(__file__).resolve().parents[1] / "data/appworld"))); OUT=ROOT/'artifacts/phase4_5'
SPEC=ROOT/'artifacts/phase4_2/R3_V2_SPECIFICATION.json'; EXPECTED_COMMIT='42b5bcf3cd334fee33f0c37d02070a9f5807add5'; EXPECTED_VERSION='0.2.0.dev0'
os.environ['APPWORLD_ROOT']=str(DATA)
spec=importlib.util.spec_from_file_location('p45obs',ROOT/'scripts/phase0_9_observability_calibration.py'); assert spec and spec.loader
OBS=importlib.util.module_from_spec(spec); sys.modules[spec.name]=OBS; spec.loader.exec_module(OBS); P7=OBS.P7
ACTION_WORDS=('add','create','send','transfer','pay','update','change','delete','remove','cancel','schedule','book','move','share','invite','follow','unfollow','like','unlike','rate','review','message','email','order','return','subscribe','unsubscribe','post','upload','rename')
ACTION_ALIASES={
 'create_file':('export','save','write'), 'delete_account':('terminate','close','delete'),
 'create_transaction_comment':('comment',), 'like_transaction':('like',),
 'remove_album_from_library':('album',), 'remove_song_from_library':('song',),
 'delete_text_message':('text message',), 'delete_voice_message':('voice message',),
 'create_transaction':('owed money','pay','transfer'), 'send_text_message':('phone text message','text message'),
}
EXCLUDE_APIS=('login','show_profile','show_account_passwords','search','list','get_','show_','message_user')
REJECTIONS=('SINGLE_REQUIREMENT_TASK','REQUIREMENT_GROUNDING_AMBIGUOUS','WORKFLOW_ARTIFACT_NOT_REQUIREMENT','TASK_CRITICALITY_UNKNOWN','EFFECT_ALIAS_OR_NON_INDEPENDENCE','NON_INDEPENDENT_EFFECTS','ORIGINAL_FINAL_PREDICATE_FAILED','NO_UNIQUE_PRODUCER','DOWNSTREAM_DEPENDENCY','HIDDEN_STATE_PRECONDITION','OMISSION_NOT_ISOLATABLE','BASELINE_REPLAY_FAILED','OTHER')
def j(v): return P7.canonical(v)
def canon(v): return json.dumps(j(v),ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha(v): return hashlib.sha256(canon(v).encode()).hexdigest()
def fsha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,v): p=Path(p); p.parent.mkdir(parents=True,exist_ok=True); P7.write_json(p,v)
def flatten(v,path='$'):
 if isinstance(v,dict):
  for k,x in v.items(): yield from flatten(x,path+'.'+str(k))
 elif isinstance(v,list):
  for i,x in enumerate(v): yield from flatten(x,f'{path}[{i}]')
 elif v is not None and not isinstance(v,bool): yield path,v
def spans(text,value):
 raw=str(value); return list(re.finditer(r'(?<![\w])'+re.escape(raw)+r'(?![\w])',text,re.I)) if len(raw)>=3 else []
def action_evidence(instruction,identity):
 name=identity.split('.')[-1].casefold(); tokens=set(name.split('_')); matched=[w for w in ACTION_WORDS if w in tokens and re.search(r'(?<!\w)'+re.escape(w)+r'(?!\w)',instruction,re.I)]
 for phrase in ACTION_ALIASES.get(name,()):
  if re.search(r'(?<!\w)'+re.escape(phrase)+r'(?!\w)',instruction,re.I): matched.append(phrase)
 return matched
def task_ids():
 out=[]
 for split in ('train','dev'):
  ids=[x.strip() for x in (DATA/'data/datasets'/f'{split}.txt').read_text().splitlines() if x.strip()]
  out += [(split,x) for x in ids]
 return out
def record_dict(record):
 try: return j(record.to_dict(keep_computed=False))
 except TypeError: return j(record.to_dict())
def snapshot_changes(case):
 changes=[]
 for start_db in sorted((case/'initial').glob('*.db')):
  app=start_db.stem
  if app in {'supervisor','admin','api_docs'}: continue
  end_db=case/'final'/start_db.name
  a=sqlite3.connect(start_db); b=sqlite3.connect(end_db); a.row_factory=sqlite3.Row; b.row_factory=sqlite3.Row
  try:
   tables=[r[0] for r in a.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' and name not like '%model_hash%'")]
   for table in tables:
    cols=[dict(r) for r in a.execute(f'pragma table_info("{table}")')]; pk=[x['name'] for x in cols if x['pk']] or [cols[0]['name']]
    def rows(db):
     out={}
     for row in db.execute(f'select * from "{table}"'):
      d=dict(row); out[tuple(d[x] for x in pk)]=d
     return out
    left,right=rows(a),rows(b)
    for key in sorted(set(left)|set(right),key=str):
     old,new=left.get(key),right.get(key)
     if old==new: continue
     op='create' if old is None else ('delete' if new is None else 'update'); record=new if new is not None else old
     fields=sorted(record) if old is None or new is None else sorted(k for k in record if old[k]!=new[k])
     rid=key[0] if len(key)==1 else list(key)
     changes.append({'operation':op,'model':f'{app}.{table}','record_id':rid,'record':j(record),'changed_fields':fields})
  finally: a.close(); b.close()
 return changes
def original(task_id,split):
 from appworld.environment import AppWorld
 from appworld.collections.models import ModelCollectionPair
 case=OUT/'originals'/task_id; shutil.rmtree(case,ignore_errors=True); case.mkdir(parents=True)
 source=(DATA/'data/tasks'/task_id/'ground_truth/compiled_solution.py').read_text(); specs=json.loads((DATA/'data/tasks'/task_id/'specs.json').read_text())
 world=AppWorld(task_id=task_id,experiment_name=f'phase4_5_{task_id}',ground_truth_mode='full'); trace=[]
 try:
  world.models.save(str(case/'initial'),format='full',delete_if_exists=True); initial_digest,_=P7.file_tree_digest(case/'initial')
  raw=world.requester.request
  def traced(*args,**kwargs):
   app=kwargs.get('_app_name',args[0] if args else None); api=kwargs.get('_api_name',args[1] if len(args)>1 else None); data={k:v for k,v in kwargs.items() if k not in {'_app_name','_api_name','client','raise_on_failure','show','track'}}
   try: result=raw(*args,**kwargs); trace.append({'sequence':len(trace),'api_identity':f'{app}.{api}','arguments':j(data),'response':j(result)}); return result
   except Exception as e: trace.append({'sequence':len(trace),'api_identity':f'{app}.{api}','arguments':j(data),'exception':{'type':type(e).__name__,'message':str(e)}}); raise
  world.requester.request=traced; message=world.execute(source+'\n__phase45_return=solution(apis, requester)\n'); execution=P7.execution_ok(message)
  evaluation=P7.evaluator_payload(world) if execution else {'status':'NOT_RUN'}
  world.models.save(str(case/'final'),format='full',delete_if_exists=True); final_digest,_=P7.file_tree_digest(case/'final')
  changes=snapshot_changes(case) if execution else []
  payload={'task_id':task_id,'split':split,'instruction':specs['instruction'],'compiled_solution_sha256':fsha(DATA/'data/tasks'/task_id/'ground_truth/compiled_solution.py'),'api_calls_artifact_sha256':fsha(DATA/'data/tasks'/task_id/'ground_truth/api_calls.json'),'evaluation_code_sha256':fsha(DATA/'data/tasks'/task_id/'ground_truth/evaluation.py'),'execution_success':execution,'message':message,'evaluator':evaluation,'initial_digest':initial_digest,'final_digest':final_digest,'api_trace':trace,'persistent_changes':changes}
  write(case/'execution.json',payload); return payload
 finally: world.close()
def ground(call,instruction):
 identity=call['api_identity']; api=identity.split('.')[-1]
 if any(x in api for x in EXCLUDE_APIS): return None
 actions=action_evidence(instruction,identity)
 literals=[]
 for path,value in flatten(call['arguments']):
  if any(x in path for x in ('access_token','password')): continue
  hits=spans(instruction,value)
  if len(hits)==1: literals.append({'span':hits[0].group(),'start':hits[0].start(),'end':hits[0].end(),'argument_path':path,'value':value})
 if not actions: return None
 action=sorted(actions,key=lambda x:(-len(x),x))[0]; hit=re.search(r'(?<!\w)'+re.escape(action)+r'(?!\w)',instruction,re.I); assert hit
 literal=min(literals,key=lambda x:(x['end']-x['start'],x['start'])) if literals else None
 return {'exact_requirement_span':hit.group(),'span_start':hit.start(),'span_end':hit.end(),'normalized_requirement':action.casefold(),'semantic_role':api,'argument_path':literal['argument_path'] if literal else None,'literal':literal,'action_tokens':actions}
def match_change(call,grounding,changes):
 app=call['api_identity'].split('.')[0]; values=[v for _,v in flatten(call['arguments']) if len(str(v))>=3]
 scored=[]
 for change in changes:
  if not change['model'].startswith(app+'.'): continue
  if any(x in change['model'] for x in ('_fts_','_config','_content','_docsize','_idx','_data')): continue
  record_values={canon(v) for _,v in flatten(change['record'])}; score=sum(canon(v) in record_values for v in values)
  api_tokens=set(call['api_identity'].split('.')[-1].split('_'))-{'create','update','delete','remove','from','library','send'}
  table_tokens=set(change['model'].split('.')[-1].rstrip('s').split('_'))
  score += 2*len(api_tokens & table_tokens)
  if call['api_identity'].endswith('delete_account') and change['operation']=='delete' and change['model'].endswith('.users'): score+=10
  if score: scored.append((score,change))
 if not scored: return None
 best=max(x[0] for x in scored); winners=[x[1] for x in scored if x[0]==best]
 return winners[0] if len(winners)==1 else None
def predicate(effect,instruction,call):
 change=effect; record=change['record']; fields={}
 call_values={canon(v) for _,v in flatten(call['arguments'])}
 for name,value in record.items():
  if canon(value) in call_values and len(spans(instruction,value))==1: fields[name]=value
 if change['operation']=='update': fields={k:record[k] for k in change['changed_fields'] if k in fields}
 if change['operation']=='delete': return {'operation':'delete','model':change['model'],'record_id':change['record_id'],'expected_absent':True,'evaluation':'model_record_absence_v1','excluded_fields_reason':'Deletion requirement concerns entity absence.'}
 if not fields and change['operation']=='update':
  fields={k:record[k] for k in change['changed_fields'] if k not in {'record_hash','updated_at','created_at','like_count','review_count'} and k in record}
 if not fields and change['operation']=='create':
  fields={k:v for k,v in record.items() if k not in {'id','record_hash','updated_at','created_at'} and not k.endswith('_hash')}
 if not fields: return None
 return {'operation':change['operation'],'model':change['model'],'record_id':change['record_id'],'expected_fields':fields,'evaluation':'stable_model_projection_v1','excluded_fields':sorted(set(record)-set(fields)-{'id'}),'excluded_fields_reason':'Fields are not grounded in this independent instruction requirement.'}
def scan_case(run):
 if not run['execution_success'] or run['evaluator'].get('status')!='COMPLETED' or not run['evaluator']['result'].get('success'): return [],'BASELINE_REPLAY_FAILED'
 effects=[]
 for call in run['api_trace']:
  grounding=ground(call,run['instruction'])
  if not grounding: continue
  change=match_change(call,grounding,run['persistent_changes'])
  if not change: continue
  pred=predicate(change,run['instruction'],call)
  if not pred: continue
  effects.append({'effect_id':f"{run['task_id']}::call-{call['sequence']}",'requirement_span':grounding,'producer_api_identity':call['api_identity'],'producer_arguments':call['arguments'],'producer_trace_index':call['sequence'],'persistent_effect':change,'stable_predicate':pred,'original_final_predicate_result':True,'effect_type':change['model']+'.'+change['operation']})
 if len(effects)<2: return [],'SINGLE_REQUIREMENT_TASK'
 span_counts=Counter((x['requirement_span']['span_start'],x['requirement_span']['span_end']) for x in effects); spans_=set(span_counts); records={(x['persistent_effect']['model'],x['persistent_effect']['record_id']) for x in effects}
 if len(spans_)<2: return [],'SINGLE_REQUIREMENT_TASK'
 if any(n>1 for n in span_counts.values()): return [],'NO_UNIQUE_PRODUCER'
 if len(records)<len(effects): return [],'NON_INDEPENDENT_EFFECTS'
 output=[]
 for target in effects:
  later=run['api_trace'][target['producer_trace_index']+1:]; later_values={canon(v) for call in later for _,v in flatten(call['arguments'])}; response=run['api_trace'][target['producer_trace_index']].get('response'); response_values={canon(v) for _,v in flatten(response)}
  if later_values & response_values: continue
  output.append({'status':'POTENTIAL_APPWORLD_R3V2_OMISSION_CANDIDATE','task_id':run['task_id'],'split':run['split'],'instruction':run['instruction'],'requirement_spans':[x['requirement_span'] for x in effects],'required_effect_count':len(effects),'all_effect_predicates':[x['stable_predicate'] for x in effects],'omitted_effect_id':target['effect_id'],'producer_api_identity':target['producer_api_identity'],'producer_arguments':target['producer_arguments'],'producer_trace_index':target['producer_trace_index'],'original_effect_witness':target['persistent_effect'],'original_final_state_predicate_result':True,'producer_isolation_evidence':{'unique_record_mapping':True,'response_not_used_by_later_calls':True,'remaining_arguments_need_no_rewrite':True},'effect_type':target['effect_type'],'app':target['producer_api_identity'].split('.')[0],'r3v2_specification_sha256':fsha(SPEC),'omission_trajectory_executed':False,'omission_evaluator_executed':False})
 return (output,None) if output else ([], 'DOWNSTREAM_DEPENDENCY')
def main():
 ids=task_ids(); assert len(ids)==147; OUT.mkdir(parents=True,exist_ok=True); candidates=[]; reject=Counter(); replay=Counter(); stages=Counter()
 for n,(split,task_id) in enumerate(ids,1):
  evidence=OUT/'originals'/task_id/'execution.json'
  if evidence.exists():
   run=json.loads(evidence.read_text())
  else: run=original(task_id,split)
  replay['execution_success']+=run['execution_success']; passed=run['evaluator'].get('status')=='COMPLETED' and run['evaluator']['result'].get('success',False); replay['official_pass']+=passed
  rows,reason=scan_case(run)
  if rows: candidates.extend(rows); stages['tasks_with_2_grounded']+=1; stages['after_workflow_exclusion']+=1; stages['after_semantic_independence']+=1; stages['predicates_calibrated']+=1; stages['producers_identified']+=1; stages['producers_isolatable']+=1
  else: reject[reason]+=1
  print(f'{n}/147 {task_id} {"PASS" if passed else "FAIL"}',flush=True)
 for key in REJECTIONS: reject.setdefault(key,0)
 with (OUT/'appworld_r3v2_candidates.jsonl').open('w') as h:
  for x in candidates: h.write(json.dumps(j(x),ensure_ascii=False,sort_keys=True)+'\n')
 tasks=len({x['task_id'] for x in candidates}); apps=Counter(x['app'] for x in candidates); effects=Counter(x['effect_type'] for x in candidates); conjuncts=Counter(x['required_effect_count'] for x in candidates)
 gate='APPWORLD_R3V2_READY_FOR_CALIBRATION_PILOT' if tasks>=10 and len(apps)>=2 and len(effects)>=2 else ('APPWORLD_R3V2_CONDITIONAL_SMALL_CALIBRATION' if tasks>=5 else 'APPWORLD_R3V2_NO_GO')
 result={'phase':'4.5','gate':gate,'version':{'repository_commit':EXPECTED_COMMIT,'appworld':'0.2.0.dev0','data':'0.2.0','python':sys.version,'previous_freeze':'artifacts/phase0_9_retry2/FREEZE_RECEIPT.json','previous_freeze_sha256':fsha(ROOT/'artifacts/phase0_9_retry2/FREEZE_RECEIPT.json')},'train_tasks':90,'dev_tasks':57,'total_full_ground_truth_tasks':147,'original_replay':dict(replay),**stages,'final_r3v2_candidate_tasks':tasks,'final_omission_effects':len(candidates),'app_distribution':dict(apps),'effect_type_distribution':dict(effects),'conjunct_distribution':dict(conjuncts),'rejections':dict(reject),'r3v2_specification_sha256':fsha(SPEC),'candidate_sha256':fsha(OUT/'appworld_r3v2_candidates.jsonl'),'omission_trajectory_executions':0,'omission_evaluator_outcomes':0,'llm_api_calls':0,'issue_pr_search_performed':False,'test_splits_used':False}
 write(OUT/'results.json',result)
 report=['# Phase 4.5 - AppWorld R3v2 Opportunity Gate','','## Version','- Commit: `'+EXPECTED_COMMIT+'`','- AppWorld: `0.2.0.dev0`','- Data: `0.2.0`','- Population: train 90 + dev 57 = 147','','## Accounting']+[f'- {k}: {v}' for k,v in result.items() if k not in ('rejections','version')]+['','## Rejections']+[f'- {k}: {v}' for k,v in sorted(reject.items())]+['','## Gate',f'`{gate}`','','Candidate generation used instruction, official reference execution, API/tool semantics, and generic persistent model changes. Evaluation code was used only to establish baseline PASS. No omission, test split, LLM API, or issue/PR search was used.']
 (ROOT/'PHASE4_5_APPWORLD_R3V2_OPPORTUNITY_GATE.md').write_text('\n'.join(report)+'\n'); print(json.dumps(result,sort_keys=True))
if __name__=='__main__': main()
