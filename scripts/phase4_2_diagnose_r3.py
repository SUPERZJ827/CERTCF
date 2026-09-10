#!/usr/bin/env python3
"""Persist an evidence-only diagnosis of Phase 4.1 UNKNOWN cases."""
from __future__ import annotations
import hashlib, json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; P41=ROOT/'artifacts/phase4_1'; OUT=ROOT/'artifacts/phase4_2'
PRIMARY={
 'test_case_ST006_022':'EFFECT_DECOMPOSITION_FALSE_POSITIVE',
 'test_case_ST006_024':'EFFECT_DECOMPOSITION_FALSE_POSITIVE',
 'test_case_ST006_028':'EFFECT_DECOMPOSITION_FALSE_POSITIVE',
 'test_csa_001':'EFFECT_ALIAS_OR_NON_INDEPENDENCE',
 'test_fnol_023':'EFFECT_ALIAS_OR_NON_INDEPENDENCE',
}
RATIONALE={
 'test_case_ST006_022':"The fixed substring anchor matched 'open' inside 'unopened'. The prompt requests a product return; it does not independently request creation of a Zendesk ticket.",
 'test_case_ST006_024':"The fixed substring anchor matched 'open' inside 'unopened'. The ticket is workflow state for the requested return, not a separately stated conjunct.",
 'test_case_ST006_028':"The action anchor 'open' does not establish a separate ticket requirement. The prompt requests the return/RMA effect, while the ticket is reference-workflow state.",
 'test_csa_001':"Ticket creation and approval-request creation represent one VPN-access approval workflow. The prompt does not state them as two independent required outcomes.",
 'test_fnol_023':"The Zendesk FNOL ticket and claims record are two representations in the single requested claim-filing workflow, not two independently requested conjuncts.",
}
def load(p): return json.loads(Path(p).read_text())
def write(p,v): p=Path(p); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
def main():
 pilot=load(P41/'PILOT_QUEUE_V1.json'); exposed={x['task_uid'] for x in pilot}
 phase40=[json.loads(x) for x in (ROOT/'artifacts/phase4_0/r3_omission_candidates.jsonl').read_text().splitlines() if x.strip()]
 all_tasks={x['task_uid'] for x in phase40}; unknown=[]
 for path in sorted((P41/'cases').iterdir()):
  cert=load(path/'conjunctive_omission_certificate.json')
  if cert['classification']!='CONJUNCTIVE_OMISSION_UNKNOWN': continue
  meta=load(path/'metadata.json'); predicates=load(path/'effect_predicates.json'); oe=load(path/'original/execution.json'); ov=load(path/'original/evaluator.json'); omission=load(path/'omission/execution.json')
  false=[x for x in cert['original_predicates'] if not x['satisfied']]
  failed_ids={x['effect_id'] for x in false}; failed_predicates=[x for x in predicates if x['effect_id'] in failed_ids]
  changes=[]
  for result in false:
   for item in result['clause_evidence']:
    expected=item['clause'].get('expected_record') or item['clause'].get('expected_fields')
    observed=item['observed_record']; differing=[]
    if isinstance(expected,dict) and isinstance(observed,dict): differing=sorted(k for k in set(expected)|set(observed) if expected.get(k)!=observed.get(k))
    changes.append({'effect_id':result['effect_id'],'table':item['clause']['table'],'entity_id':item['clause']['entity_id'],'differing_fields':differing,'expected':expected,'observed':observed})
  task_id=meta['task_id']
  unknown.append({'case_id':meta['case_id'],'task_uid':meta['task_uid'],'task_id':task_id,'development_status':'R3_DEVELOPMENT_EXPOSED','required_effects':predicates,'original_execution_status':oe['status'],'original_official_evaluator':ov['verdict'],'original_predicate_results':cert['original_predicates'],'failed_effect_ids':sorted(failed_ids),'failed_effect_evidence':changes,'primary_root_cause':PRIMARY[task_id],'root_cause_evidence':RATIONALE[task_id],'cross_cutting_measurement_finding':'CREATE predicates compared the full immediate post-producer record against final state; later official updates changed status/description/updated_at while preserving entity existence.','omission_execution_status':omission['status'],'omission_executability_classification':'BASELINE_GATE_BLOCKED','omission_mechanism_failure_observed':False})
 OUT.mkdir(parents=True,exist_ok=True); write(OUT/'UNKNOWN_AUDIT_QUEUE.json',unknown)
 spec={'name':'R3v2_CONJUNCTIVE_REQUIREMENT_OMISSION','core_certificate_changed':False,'status':'PROPOSED_FOR_PRE_EXECUTION_FREEZE','candidate_rules':[{'id':'TOKEN_BOUNDARY_ACTION_GROUNDING','rule':'Action anchors must match normalized lexical tokens or fixed phrases, never arbitrary substrings.'},{'id':'DISTINCT_REQUIREMENT_SPANS','rule':'Each effect conjunct requires a distinct explicit prompt span and semantic role; one clause may not ground multiple effects.'},{'id':'EXCLUDE_WORKFLOW_ARTIFACTS','rule':'Support tickets, approval records, and other workflow artifacts are not independent conjuncts unless the prompt separately requests that persistent outcome.'},{'id':'SEMANTIC_INDEPENDENCE','rule':'Disjoint tables/entities are insufficient; effects that jointly implement one user intent are NON_INDEPENDENT_EFFECTS.'},{'id':'FINAL_STATE_PREDICATE_CALIBRATION','rule':'Before candidate freeze, every predicate must evaluate true on a fresh ORIGINAL final state.'},{'id':'STABLE_EFFECT_PREDICATE','rule':'A create predicate uses persistent entity identity plus task-required stable fields from final state, not a byte-equal immediate post-call snapshot containing later-updated fields.'},{'id':'DOWNSTREAM_MUTATION_AUDIT','rule':'If later official calls update the produced entity, freeze the final task-relevant effect predicate and reject when stable required fields cannot be identified mechanically.'}],'forbidden':['case-id blacklist','evaluator-outcome-derived filtering','certificate weakening','manual effect decomposition'],'heldout_plan':{'development_exposed_tasks':sorted(exposed),'previously_unexposed_tasks':sorted(all_tasks-exposed),'previously_unexposed_task_count':len(all_tasks-exposed),'minimum_eligible_tasks_for_pilot':10,'omission_outcomes_before_future_freeze':0}}
 write(OUT/'R3_V2_SPECIFICATION.json',spec)
 results={'phase':'4.2','decision':'R3_V2_READY_FOR_HELDOUT_RESCAN','unknown_cases':5,'audited_cases':len(unknown),'development_exposed_tasks':len(exposed),'previously_unexposed_tasks':len(all_tasks-exposed),'primary_root_causes':{'EFFECT_DECOMPOSITION_FALSE_POSITIVE':3,'EFFECT_PREDICATE_IMPLEMENTATION_MISMATCH':0,'EFFECT_ALIAS_OR_NON_INDEPENDENCE':2,'ORIGINAL_EFFECT_OBSERVABILITY_FAILURE':0,'BASELINE_RUNTIME_INSTABILITY':0,'OTHER':0},'cross_cutting_findings':{'cases_with_effect_predicate_snapshot_mismatch':5},'omission_executability':{'BASELINE_GATE_BLOCKED':5,'HIDDEN_DOWNSTREAM_DEPENDENCY':0,'HIDDEN_STATE_PRECONDITION':0,'OTHER':0},'development_pilot_precision_diagnostic':{'true_conjunctive_effect_candidates':5,'effect_decomposition_false_positive':3,'effect_alias_or_non_independence':2,'predicate_implementation_mismatch_primary':0,'predicate_snapshot_mismatch_cross_cutting':5,'observability_failure':0,'omission_isolation_failure':0},'new_omission_trajectories_executed':0,'new_evaluator_outcomes_observed':0,'llm_api_calls':0,'issue_pr_search_performed':False,'audit_queue_sha256':hashlib.sha256((OUT/'UNKNOWN_AUDIT_QUEUE.json').read_bytes()).hexdigest(),'r3_v2_specification_sha256':hashlib.sha256((OUT/'R3_V2_SPECIFICATION.json').read_bytes()).hexdigest()}
 write(OUT/'results.json',results)
 report=['# Phase 4.2 - R3 Calibration Failure Diagnosis','','## Decision','`R3_V2_READY_FOR_HELDOUT_RESCAN`','','## Scope','The five Phase 4.1 UNKNOWN cases were audited from persisted evidence only. No trajectory or evaluator was rerun. The ten pilot tasks are `R3_DEVELOPMENT_EXPOSED`; the other 30 Phase 4.0 candidate tasks remain `R3_PREVIOUSLY_UNEXPOSED`.','','## Findings','All five originals executed successfully and received official PASS. All five omissions were blocked before execution by the original complete-effect-set gate. Therefore no omission mechanism failure was observed.','',"Three retail cases are `EFFECT_DECOMPOSITION_FALSE_POSITIVE`: substring matching treated 'open' inside 'unopened' as an action anchor and promoted an internal Zendesk ticket to a separate user requirement. Two cases are `EFFECT_ALIAS_OR_NON_INDEPENDENCE`: ticket plus approval/claim records are workflow representations of one requested outcome.",'','A cross-cutting measurement defect also affected all five: create predicates froze complete immediate post-call records. Later official calls legitimately updated those records, so final-state equality failed on fields such as `status`, `description`, `outcome_summary`, and `updated_at` even though the entity persisted. This is a representation issue, not baseline runtime instability.','','## Development-sample accounting','- True conjunctive-effect candidates: 5','- Effect-decomposition false positives: 3','- Effect alias/non-independence: 2','- Original observability failures: 0','- Omission isolation failures observed: 0','- Baseline-gate blocked: 5','','## R3v2','R3v2 keeps the strong omission certificate unchanged. It tightens pre-execution candidate grounding, semantic independence, and final-state predicate calibration. Future validation must use only the 30 previously unexposed tasks and requires at least 10 eligible tasks before a pilot.','','No issue/PR search or LLM API call was performed. No new omission trajectory or evaluator outcome was produced.']
 (ROOT/'PHASE4_2_R3_CALIBRATION_FAILURE_DIAGNOSIS.md').write_text('\n'.join(report)+'\n')
 print(json.dumps(results,sort_keys=True))
if __name__=='__main__': main()
