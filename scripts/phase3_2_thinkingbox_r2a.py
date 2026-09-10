#!/usr/bin/env python3
"""Frozen full-population orchestration for ThinkingBox R2A."""
from __future__ import annotations
import argparse, asyncio, importlib.metadata, json, platform
from collections import Counter
from pathlib import Path

try:
 import scripts.phase3_1_thinkingbox_r2a as p31
except ModuleNotFoundError:
 import phase3_1_thinkingbox_r2a as p31

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/phase3_2'
P31=ROOT/'artifacts/phase3_1'
SOURCE=P31/'R2A_VIABLE_POPULATION_V1.json'
IDENTITY_FIELDS=('task_id','domain','target_call','target_argument','original_value','mutation_value','state_effect','original_effect_witness','mutation_rule_version')

def load(path): return json.loads(Path(path).read_text())
def identity(record): return {key:record[key] for key in IDENTITY_FIELDS}
def verify_population(records):
 if len(records)!=30 or len({p31.uid(x) for x in records})!=17: return False
 encoded=[p31.canon(identity(x)) for x in records]
 return len(encoded)==len(set(encoded))
def freeze():
 receipt=OUT/'FREEZE_RECEIPT.json'
 if receipt.exists(): raise SystemExit('FREEZE_RECEIPT already exists; refusing overwrite')
 records=load(SOURCE)
 if not verify_population(records): raise SystemExit('Phase 3.1 viable population identity mismatch')
 OUT.mkdir(parents=True,exist_ok=True)
 population=OUT/'POPULATION_V1.json'; population.write_bytes(SOURCE.read_bytes())
 if p31.fsha(population)!=p31.fsha(SOURCE): raise SystemExit('population byte identity mismatch')
 prior=load(P31/'FREEZE_RECEIPT.json')
 specs={
  'mutation_rules':P31/'R2A_MUTATION_RULES_THINKINGBOX_V1.json',
  'mcp_adapter':P31/'MCP_ADAPTER_V1.txt',
  'instrumentation':P31/'REQUEST_RESPONSE_INSTRUMENTATION_V1.txt',
  'state_canonicalization':P31/'STATE_CANONICALIZATION_V1.txt',
  'task_critical_effect_specification':P31/'TASK_CRITICAL_EFFECT_SPECIFICATION_V1.txt',
  'certificate_specification':P31/'VIOLATION_CERTIFICATE_SPECIFICATION_V1.txt',
  'evaluator_relation':P31/'EVALUATOR_RELATION_SPECIFICATION_V1.txt',
 }
 receipt_data={
  'phase':'3.2','parent_phase':'3.1','thinkingbox_commit':prior['thinkingbox_commit'],
  'thinkingbox_data_release':prior['thinkingbox_data_release'],'thinkingbox_data_commit':prior['thinkingbox_data_commit'],
  'source_viable_population_sha256':p31.fsha(SOURCE),'full_population_sha256':p31.fsha(population),
  'execution_runner_sha256':p31.fsha(Path(__file__)),'phase3_1_execution_implementation_sha256':p31.fsha(ROOT/'scripts/phase3_1_thinkingbox_r2a.py'),
  'population_was_defined_before_phase3_1_outcomes':True,'population_arguments':30,'population_tasks':17,
  'r2a_changed_since_phase3_1':False,'mutation_rules_changed':False,'effect_certificate_changed':False,'evaluator_relation_changed':False,
  'issue_pr_search_performed':False,'llm_api_calls':0,
  'environment':{'python':__import__('sys').version,'platform':platform.platform(),'thinkingbox_package':importlib.metadata.version('thinkingbox'),'mcp_package':importlib.metadata.version('mcp'),'typesense_python':importlib.metadata.version('typesense'),'typesense_server_required':'30.1'},
 }
 for name,path in specs.items(): receipt_data[name+'_sha256']=p31.fsha(path)
 p31.write(receipt,receipt_data,True)
 print(json.dumps({'frozen':True,'population':30,'tasks':17,'sha256':p31.fsha(population)}))

def aggregate():
 cases=[]
 for path in sorted((OUT/'cases').glob('*')):
  m=load(path/'metadata.json'); c=load(path/'task_critical_effect_certificate.json'); x=load(path/'mutation_exactness_certificate.json')
  oe=load(path/'original/execution.json'); pe=load(path/'perturbed/execution.json'); cases.append((m,c,x,oe,pe))
 cls=Counter(x[1]['classification'] for x in cases); sensitivity=Counter(x[0]['sensitivity_classification'] for x in cases if x[0]['sensitivity_classification'])
 counts={
  'population':30,'attempted':len(cases),'baseline_replay_success':sum(x[0]['baseline_success'] for x in cases),
  'original_effect_witness_success':sum(x[0]['original_effect_witness_success'] for x in cases),
  'mutation_exactness_success':sum(x[2]['status']=='MUTATION_EXACTNESS_SUCCESS' for x in cases),
  'perturbed_execution_success':sum(x[4]['status']=='SUCCESS' for x in cases),
  'evaluator_comparisons':sum(x[0]['sensitivity_classification'] is not None for x in cases),
 }
 for key in ('TASK_CRITICAL_VIOLATION_CERTIFIED','TASK_CRITICAL_VIOLATION_FAILED','TASK_CRITICAL_VIOLATION_UNKNOWN','BEHAVIORALLY_EQUIVALENT_MUTATION','COMPENSATED_EFFECT','MUTATION_EXECUTION_INVALID'): counts[key]=cls[key]
 for key in ('SENSITIVE','R2A_FALSE_ACCEPTANCE_CANDIDATE'): counts[key]=sensitivity[key]
 by_task={}
 for row in cases: by_task.setdefault(row[0]['task_uid'],[]).append(row)
 task_counts={
  'population_tasks':17,
  'tasks_with_at_least_one_certified_violation':sum(any(x[1]['classification']=='TASK_CRITICAL_VIOLATION_CERTIFIED' for x in rows) for rows in by_task.values()),
  'tasks_with_only_uncertified_mutations':sum(all(x[1]['classification']!='TASK_CRITICAL_VIOLATION_CERTIFIED' for x in rows) for rows in by_task.values()),
  'tasks_with_at_least_one_false_acceptance_candidate':sum(any(x[0]['sensitivity_classification']=='R2A_FALSE_ACCEPTANCE_CANDIDATE' for x in rows) for rows in by_task.values()),
  'tasks_with_all_certified_mutations_sensitive':sum(any(x[1]['classification']=='TASK_CRITICAL_VIOLATION_CERTIFIED' for x in rows) and all(x[0]['sensitivity_classification'] in (None,'SENSITIVE') for x in rows) for rows in by_task.values()),
 }
 strata={}
 for field in ('domain','effect_type','argument_type'):
  strata[field]={}
  for value in sorted({x[0][field] for x in cases}):
   rows=[x for x in cases if x[0][field]==value]
   strata[field][value]={'candidates':len(rows),'certified':sum(x[1]['classification']=='TASK_CRITICAL_VIOLATION_CERTIFIED' for x in rows),'sensitive':sum(x[0]['sensitivity_classification']=='SENSITIVE' for x in rows),'false_acceptance_candidates':sum(x[0]['sensitivity_classification']=='R2A_FALSE_ACCEPTANCE_CANDIDATE' for x in rows)}
 if counts['R2A_FALSE_ACCEPTANCE_CANDIDATE']:
  gate='THINKINGBOX_FULL_POPULATION_DISCOVERY_SIGNAL_PRESENT'
 elif counts['TASK_CRITICAL_VIOLATION_CERTIFIED']/30>=0.5 and counts['evaluator_comparisons']==counts['SENSITIVE']:
  gate='THINKINGBOX_FULL_POPULATION_FULLY_SENSITIVE'
 else: gate='THINKINGBOX_FULL_POPULATION_INCONCLUSIVE'
 return {'phase':'3.2','gate':gate,'candidate_level':counts,'task_level':task_counts,'stratification':strata,'case_ids':[x[0]['case_id'] for x in cases],'contamination':{'issue_pr_search_performed':False,'llm_api_calls':0}}

async def run():
 receipt=load(OUT/'FREEZE_RECEIPT.json')
 if receipt['execution_runner_sha256']!=p31.fsha(Path(__file__)): raise SystemExit('runner differs from freeze')
 records=load(OUT/'POPULATION_V1.json')
 if not verify_population(records) or p31.fsha(OUT/'POPULATION_V1.json')!=receipt['full_population_sha256']: raise SystemExit('population differs from freeze')
 if (OUT/'cases').exists() and any((OUT/'cases').iterdir()): raise SystemExit('case evidence exists; refusing overwrite')
 tasks=p31.taskmap(); _,modules=p31.systems(); old=p31.OUT; p31.OUT=OUT
 try:
  for record in records: print(await p31.run_case(record,tasks,modules),flush=True)
 finally: p31.OUT=old
 result=aggregate(); p31.write(OUT/'results.json',result)
 lines=['# Phase 3.2 - ThinkingBox R2A Frozen Full-Population Replay','','## Gate',f"`{result['gate']}`",'','## Candidate-level accounting']+[f"- {k}: {v}" for k,v in result['candidate_level'].items()]+['','## Task-level accounting']+[f"- {k}: {v}" for k,v in result['task_level'].items()]+['','## Interpretation','The 30 candidates belong to 17 tasks and are not treated as 30 independent task samples.','No issue/PR search or LLM API call was performed. No R2A rule, mutation, certificate, or evaluator relation was changed.']
 (ROOT/'PHASE3_2_THINKINGBOX_R2A_FULL_POPULATION.md').write_text('\n'.join(lines)+'\n')
 print(json.dumps({'gate':result['gate'],'candidate_level':result['candidate_level'],'task_level':result['task_level']},sort_keys=True))

if __name__=='__main__':
 parser=argparse.ArgumentParser(); parser.add_argument('command',choices=('freeze','run')); args=parser.parse_args()
 freeze() if args.command=='freeze' else asyncio.run(run())
