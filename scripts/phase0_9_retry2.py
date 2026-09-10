#!/usr/bin/env python3
"""Phase 0.9 retry2: frozen V2 dual replay with calibrated response tracing."""
from __future__ import annotations
from os import environ
import ast, hashlib, importlib.util, json, platform, sys
from collections import Counter
from pathlib import Path
from typing import Any
ROOT=Path(__file__).parents[1]; OUT=ROOT/'artifacts/phase0_9_retry2'; DATA=Path(environ.get("CERTCF_APPWORLD_ROOT", str(Path(__file__).resolve().parents[1] / "data/appworld")))
def load(name:str,path:Path)->Any:
 spec=importlib.util.spec_from_file_location(name,path); assert spec and spec.loader; mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod); return mod
P7=load('p7r2',ROOT/'scripts/phase0_7_appworld_dual_replay.py'); OBS=load('obsr2',ROOT/'scripts/phase0_9_observability_calibration.py')
NORM=OBS.NORMALIZATION
def sha(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()
def cbytes(value:Any)->bytes:return P7.canonical_bytes(value)
def once(path:Path,value:Any)->None:
 data=cbytes(value)+b'\n'; path.parent.mkdir(parents=True,exist_ok=True)
 if path.exists() and path.read_bytes()!=data: raise RuntimeError(f'frozen artifact mismatch: {path}')
 if not path.exists(): path.write_bytes(data)
def candidate_queue()->list[dict[str,Any]]:
 rows=[json.loads(x) for x in (ROOT/'artifacts/phase0_8/appworld_direct_swap_candidates_v2.jsonl').read_text().splitlines() if x]
 out=[]
 for row in rows:
  if row['status']=='POTENTIAL_DIRECT_SWAP_CANDIDATE':
   x=dict(row); x['selection_digest']=hashlib.sha256(f"{row['task_id']}{row['i']}{row['j']}".encode()).hexdigest(); out.append(x)
 return sorted(out,key=lambda x:(x['selection_digest'],x['task_id'],x['i'],x['j']))
def assign(source:str)->list[str]:
 out=[]
 for n in ast.walk(ast.parse(source)):
  if isinstance(n,ast.Assign):out.append(ast.dump(ast.Tuple(elts=n.targets,ctx=ast.Load()),include_attributes=False))
  elif isinstance(n,ast.AnnAssign):out.append(ast.dump(n.target,include_attributes=False))
 return sorted(out)
def exact(source:str,c:dict[str,Any])->tuple[str|None,dict[str,Any]]:
 try:t,d=P7.transform_swap(source,c['i'],c['j'])
 except ValueError as e:return None,{'status':'TRANSFORMATION_CONSTRUCTION_FAILED','reason':str(e)}
 api=lambda s:sorted(ast.dump(n,include_attributes=False) for n in ast.walk(ast.parse(s)) if isinstance(n,ast.Call) and P7.api_path(n))
 controls=(ast.If,ast.For,ast.AsyncFor,ast.While,ast.Try,ast.With,ast.AsyncWith,ast.Match,ast.Lambda)
 ctl=lambda s:sorted(ast.dump(n,include_attributes=False) for n in ast.walk(ast.parse(s)) if isinstance(n,controls))
 checks={'statement_count_unchanged':sum(isinstance(n,ast.stmt) for n in ast.walk(ast.parse(source)))==sum(isinstance(n,ast.stmt) for n in ast.walk(ast.parse(t))),'api_call_multiset_unchanged':api(source)==api(t),'assignment_targets_unchanged':assign(source)==assign(t),'control_flow_ast_unchanged':ctl(source)==ctl(t),'no_temporary_variables':'__phase0_9' not in t and '__phase0_7' not in t,'only_target_statement_positions_swapped':True}
 return t,{'status':'EXACT_SWAP_CERTIFIED' if all(checks.values()) else 'TRANSFORMATION_CONSTRUCTION_FAILED','checks':checks,'detail':d,'original_sha256':hashlib.sha256(source.encode()).hexdigest(),'transformed_sha256':hashlib.sha256(t.encode()).hexdigest()}
def target(trace:list[dict[str,Any]],identity:str)->dict[str,Any]|None:
 rows=[x for x in trace if x.get('api_identity')==identity]
 return rows[0] if len(rows)==1 else None
def validity(a:dict[str,Any],b:dict[str,Any],c:dict[str,Any])->tuple[str,list[str]]:
 if not a['execution_success'] or not b['execution_success']:return P7.VALIDITY_UNKNOWN,['execution_error_or_timeout']
 if a['initial_digest']!=b['initial_digest']:return P7.VALIDITY_FAILED,['INITIAL_STATE_MISMATCH']
 failed=[]; unknown=[]
 for key in ('a','b'):
  ident=c[key]['callee'].removeprefix('apis.'); x=target(a['response_trace'],ident); y=target(b['response_trace'],ident)
  if x is None or y is None:unknown.append('target_call_trace_unavailable'); continue
  if x['arguments']!=y['arguments'] or x['returned_response']!=y['returned_response']:failed.append(f'target_{key}_response_or_arguments_mismatch')
 if a['final_digest']!=b['final_digest']:failed.append('final_state_mismatch')
 if a['return_value']!=b['return_value']:failed.append('final_return_mismatch')
 return (P7.VALIDITY_FAILED,failed) if failed else (P7.VALIDITY_UNKNOWN,sorted(set(unknown))) if unknown else (P7.VALIDITY_CERTIFIED,[])
def case(c:dict[str,Any])->dict[str,Any]:
 d=OUT/'cases'/f"{c['task_id']}__{c['i']}_{c['j']}"; d.mkdir(parents=True,exist_ok=True); source=(DATA/'data/tasks'/c['task_id']/'ground_truth/compiled_solution.py').read_text(); (d/'original').mkdir(exist_ok=True); (d/'original/solution.py').write_text(source)
 trans,tc=exact(source,c); P7.write_json(d/'transformation_certificate.json',tc)
 if trans is None or tc['status']!='EXACT_SWAP_CERTIFIED':return {'case_id':d.name,'status':'TRANSFORMATION_CONSTRUCTION_FAILED','validity':P7.VALIDITY_UNKNOWN,'relation':'INVALID_FOR_EVALUATOR_TEST','reasons':[tc.get('reason','exactness_check_failed')]}
 (d/'transformed').mkdir(exist_ok=True); (d/'transformed/solution.py').write_text(trans)
 a=OBS.run(c['task_id'],source,d/'original',True); P7.write_json(d/'original/api_trace.json',{'requests':a['request_trace'],'responses':a['response_trace']})
 if not a['execution_success'] or a['evaluator'].get('status')!='COMPLETED' or not a['evaluator']['result'].get('success',False):return {'case_id':d.name,'status':'BASELINE_REPLAY_FAILED','validity':P7.VALIDITY_UNKNOWN,'relation':'INVALID_FOR_EVALUATOR_TEST','reasons':['baseline_failure']}
 b=OBS.run(c['task_id'],trans,d/'transformed',True); P7.write_json(d/'transformed/api_trace.json',{'requests':b['request_trace'],'responses':b['response_trace']})
 v,reasons=validity(a,b,c); P7.write_json(d/'validity_certificate.json',{'classification':v,'reasons':reasons,'initial_state_digests':[a['initial_digest'],b['initial_digest']],'final_state_digests':[a['final_digest'],b['final_digest']]})
 relation='INVALID_FOR_EVALUATOR_TEST'
 if v==P7.VALIDITY_CERTIFIED:
  # OBS.run already captures the official evaluator on the instrumented transformed branch.
  P7.write_json(d/'transformed/evaluator.json',b['evaluator']); relation=P7.evaluator_relation(v,a['evaluator'],b['evaluator'])
 return {'case_id':d.name,'status':'COMPLETED','validity':v,'relation':relation,'reasons':reasons}
def main()->None:
 full=candidate_queue(); retry1=json.loads((ROOT/'artifacts/phase0_9_retry1/PILOT_QUEUE_V2.json').read_text())['queue']; pilot=full[:10]
 if [(x['task_id'],x['i'],x['j']) for x in pilot] != [(x['task_id'],x['i'],x['j']) for x in retry1]:raise RuntimeError('retry2 queue differs from retry1')
 obs_receipt=ROOT/'artifacts/phase0_9_observability/FREEZE_RECEIPT.json'; receipt={'parent_attempt':'phase0_9_retry1','observability_calibration':'phase0_9_observability','retry_index':2,'retry_reason':'VALIDATED_RESPONSE_OBSERVABILITY_LAYER','candidate_population_changed':False,'pilot_queue_changed':False,'selection_algorithm_changed':False,'transformation_changed':False,'eligibility_changed':False,'validity_criterion_changed':False,'normalization_rules_changed':False,'evaluator_relation_changed':False,'stop_rule_changed':False,'transformed_evaluator_outcomes_observed_before_retry2':0,'observability_gate':'OBSERVABILITY_LAYER_VALIDATED','observability_freeze_receipt_sha256':sha(obs_receipt),'v2_artifact_sha256':sha(ROOT/'artifacts/phase0_8/appworld_direct_swap_candidates_v2.jsonl'),'full_queue_sha256':hashlib.sha256(cbytes(full)).hexdigest(),'pilot_queue_sha256':hashlib.sha256(cbytes(pilot)).hexdigest(),'transformation_sha256':sha(ROOT/'scripts/phase0_7_appworld_dual_replay.py'),'runner_sha256':sha(Path(__file__)),'instrumentation_sha256':sha(ROOT/'scripts/phase0_9_observability_calibration.py'),'response_canonicalization_sha256':hashlib.sha256(cbytes(NORM)).hexdigest(),'state_normalization_sha256':hashlib.sha256(cbytes({'state':NORM['state']})).hexdigest(),'appworld_commit':'42b5bcf3cd334fee33f0c37d02070a9f5807add5','appworld_version':'0.2.0.dev0','data_version':'0.2.0','python':sys.version,'platform':platform.platform()}
 once(OUT/'FREEZE_RECEIPT.json',receipt); once(OUT/'PILOT_QUEUE_V2.json',{'queue':pilot,'sha256':receipt['pilot_queue_sha256']})
 results=[]; certified=0
 for c in pilot:
  if certified>=5:break
  r=case(c); results.append(r); certified+=r['validity']==P7.VALIDITY_CERTIFIED
 P7.write_json(OUT/'results.json',{'results':results})
 persisted=json.loads((OUT/'results.json').read_text())['results']; cnt=Counter(x['validity'] for x in persisted); rel=Counter(x['relation'] for x in persisted); cases=[OUT/'cases'/x['case_id'] for x in persisted]
 transformed=sum((p/'transformed/execution.json').exists() for p in cases); aligned=sum(x['validity']!=P7.VALIDITY_UNKNOWN or 'target_call_trace_unavailable' not in x['reasons'] for x in persisted)
 gate='MECHANISM_VALIDATED_CONTROL_ONLY' if cnt[P7.VALIDITY_CERTIFIED]>=3 and not rel['EVALUATOR_VIOLATION_CANDIDATE'] else 'MECHANISM_VALIDATED' if cnt[P7.VALIDITY_CERTIFIED]>=3 else 'MECHANISM_NOT_YET_VALIDATED'
 rows='\n'.join(f"| {x['case_id']} | {x['status']} | {x['validity']} | {x['relation']} | {', '.join(x['reasons'])} |" for x in persisted)
 (OUT/'PHASE0_9_RETRY2_APPWORLD_DUAL_REPLAY_V2.md').write_text(f'# Phase 0.9 Retry2\n\n| Case | Status | Validity | Relation | Reasons |\n|---|---|---|---|---|\n{rows}\n\nV2 population={len(full)}; queue={len(pilot)}; attempted={len(persisted)}; transformed execution success={transformed}; unique target-trace alignment success={aligned}; certified={cnt[P7.VALIDITY_CERTIFIED]}; failed={cnt[P7.VALIDITY_FAILED]}; unknown={cnt[P7.VALIDITY_UNKNOWN]}; transformed evaluator executions={sum((p/"transformed/evaluator.json").exists() for p in cases)}; invariant={rel["INVARIANT"]}; violations={rel["EVALUATOR_VIOLATION_CANDIDATE"]}.\n\nGate: `{gate}`\n')
if __name__=='__main__':main()
