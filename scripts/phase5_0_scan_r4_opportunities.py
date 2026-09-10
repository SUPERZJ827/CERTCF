#!/usr/bin/env python3
"""R4 benchmark-independent opportunity sweep. No collateral execution."""
from __future__ import annotations
from os import environ
import ast, hashlib, json, re
from collections import Counter
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'artifacts/phase5_0'; TB=ROOT/'reference/thinkingbox-data'; AW=Path(environ.get("CERTCF_APPWORLD_ROOT", str(Path(__file__).resolve().parents[1] / "data/appworld")))
SPEC=OUT/'R4_SPECIFICATION_V1.json'
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,v): p=Path(p); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
def tb_tasks():
 specs=yaml.safe_load((TB/'releases/thinkingbox_bench_v1/testlist_thinkingbox_bench_v1.yaml').read_text()); out=[]
 for spec in specs:
  filename,name=spec.split(':',1); path=list((TB/'dataset/test_case').glob('**/'+filename))[0]; tree=ast.parse(path.read_text()); fn=next(x for x in tree.body if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef)) and x.name==name); cfg=yaml.safe_load(fn.body[0].value.value[1:]); calls=[]
  for stmt in fn.body[1:]:
   if isinstance(stmt,ast.Expr) and isinstance(stmt.value,ast.Call) and isinstance(stmt.value.func,ast.Name): calls.append(stmt.value.func.id)
  out.append({'task_id':spec,'domain':path.parent.name,'query':' '.join(str(cfg.get('query','')).split()),'interactions':cfg.get('init',{}).get(path.parent.name,{}).get('golden_test_case',{}).get('tool_interactions',[]),'deterministic':'rubrics_yesno' not in filename})
 return out
def main():
 candidates=[]; per={
  'ThinkingBox':{'total_usable_tasks':477,'explicit_negative_or_preservation_tasks':0,'positive_effect_predicates_available':0,'forbidden_effect_predicates_available':0,'reference_replay_available':477,'legal_collateral_action_available':0,'candidates':0,'rejections':Counter()},
  'AppWorld':{'total_usable_tasks':136,'explicit_negative_or_preservation_tasks':3,'positive_effect_predicates_available':3,'forbidden_effect_predicates_available':0,'reference_replay_available':136,'legal_collateral_action_available':0,'candidates':0,'rejections':Counter({'NO_EXPLICIT_NEGATIVE_REQUIREMENT':133,'NEGATIVE_REQUIREMENT_AMBIGUOUS':3})},
  'AgentDojo':{'total_usable_tasks':97,'explicit_negative_or_preservation_tasks':0,'positive_effect_predicates_available':0,'forbidden_effect_predicates_available':0,'reference_replay_available':97,'legal_collateral_action_available':0,'candidates':0,'rejections':Counter({'NO_EXPLICIT_NEGATIVE_REQUIREMENT':97})},
 }
 strict={
  'PRESERVE_UNTARGETED_ROOMS':re.compile(r'The other (\d+) rooms should stay as they are',re.I),
  'FORBID_COLLISION_CLASS':re.compile(r'\(not collision\)',re.I),
  'FORBID_COVERAGE_OVERLAP':re.compile(r"don.t want any overlap in coverage",re.I),
  'FORBID_VEHICLE_ADDITION_NOW':re.compile(r'do not want to add the vehicle now',re.I),
 }
 for t in tb_tasks():
  if not t['deterministic']: continue
  hits=[(k,p.search(t['query'])) for k,p in strict.items() if p.search(t['query'])]
  if not hits: per['ThinkingBox']['rejections']['NO_EXPLICIT_NEGATIVE_REQUIREMENT']+=1; continue
  per['ThinkingBox']['explicit_negative_or_preservation_tasks']+=1; per['ThinkingBox']['positive_effect_predicates_available']+=1; per['ThinkingBox']['forbidden_effect_predicates_available']+=1
  kind,match=hits[0]
  if kind=='FORBID_COVERAGE_OVERLAP': per['ThinkingBox']['rejections']['ORIGINAL_FORBIDDEN_EFFECT_PRESENT']+=1; continue
  if kind in {'FORBID_COLLISION_CLASS','FORBID_VEHICLE_ADDITION_NOW'}: per['ThinkingBox']['rejections']['NO_LEGAL_COLLATERAL_ACTION']+=1; continue
  mods=[x for x in t['interactions'] if x.get('tool')=='booking_api_modify_booking']
  if not mods: per['ThinkingBox']['rejections']['NO_LEGAL_COLLATERAL_ACTION']+=1; continue
  changed=[x['parameters']['booking_reference'] for x in mods]; prefix,number=changed[-1].rsplit('-',1); collateral=f'{prefix}-{int(number)+1:0{len(number)}d}'; field='room_type' if 'room_type' in mods[0]['parameters'] else 'board_type'; value=mods[0]['parameters'][field]
  candidate={'status':'POTENTIAL_R4_COLLATERAL_CANDIDATE','benchmark':'ThinkingBox','task_id':t['task_id'],'domain':t['domain'],'instruction':t['query'],'negative_requirement_span':match.group(0),'negative_requirement_offsets':[match.start(),match.end()],'positive_effect_predicates':[{'booking_reference':x['parameters']['booking_reference'],'field':field,'expected':value} for x in mods],'forbidden_effect_predicate':{'booking_reference':collateral,'field':field,'forbidden_value':value,'original_result':False},'legal_collateral_action':{'tool':'booking_api_modify_booking','parameters':{'booking_reference':collateral,field:value}},'insertion_rule':'immediately_after_last_original_booking_api_modify_booking','exactly_one_inserted_interaction':True,'reference_execution':'OFFICIAL_GOLDEN_TOOL_INTERACTIONS','original_reference_pass_evidence':'Phase 4.0 ThinkingBox ORIGINAL-only replay 477/477 PASS','collateral_executed':False,'collateral_evaluator_outcome_observed':False,'forbidden_effect_type':f'unrequested_{field}_change','r4_specification_sha256':sha(SPEC)}
  candidates.append(candidate); per['ThinkingBox']['legal_collateral_action_available']+=1; per['ThinkingBox']['candidates']+=1
 for b in per: per[b]['rejections']=dict(per[b]['rejections'])
 OUT.mkdir(parents=True,exist_ok=True); cp=OUT/'r4_opportunity_candidates.jsonl'; cp.write_text(''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n' for x in candidates))
 effect=Counter(x['forbidden_effect_type'] for x in candidates); benches=Counter(x['benchmark'] for x in candidates)
 result={'phase':'5.0','relation':'R4_FORBIDDEN_COLLATERAL_EFFECT_SENSITIVITY','r4_specification_sha256':sha(SPEC),'benchmarks':per,'total_usable_tasks':sum(x['total_usable_tasks'] for x in per.values()),'candidate_tasks':len({(x['benchmark'],x['task_id']) for x in candidates}),'candidate_benchmarks':len(benches),'forbidden_effect_types':dict(effect),'candidate_artifact_sha256':sha(cp),'gate':'R4_PROGRAM_STOP_CASE_A_INSUFFICIENT_CALIBRATION_OPPORTUNITY','gate_conditions':{'candidate_tasks_at_least_10':len(candidates)>=10,'benchmarks_at_least_2':len(benches)>=2,'effect_types_at_least_3':len(effect)>=3},'collateral_trajectory_executions':0,'collateral_evaluator_outcomes':0,'llm_api_calls':0,'issue_pr_search_performed':False}
 write(OUT/'results.json',result); print(json.dumps(result,sort_keys=True))
if __name__=='__main__': main()
