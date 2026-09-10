#!/usr/bin/env python3
"""Static ToolSandbox R3v2 feasibility accounting; never executes a trajectory."""
from __future__ import annotations
import ast, hashlib, json, platform, sys
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; REPO=ROOT/'reference/toolsandbox'; OUT=ROOT/'artifacts/phase4_4'
SPEC=ROOT/'artifacts/phase4_2/R3_V2_SPECIFICATION.json'; SCENARIOS=REPO/'tool_sandbox/scenarios'
MODULES=('single_tool_call_scenarios.py','multiple_tool_call_scenarios.py','multiple_user_turn_scenarios.py','insufficient_information_scenarios.py')
def fsha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,v): p=Path(p); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(v,sort_keys=True,indent=2)+'\n')
def calls(path):
 tree=ast.parse(path.read_text()); out=[]
 for node in ast.walk(tree):
  if not isinstance(node,ast.Call): continue
  direct=isinstance(node.func,ast.Name) and node.func.id=='ScenarioExtension'
  qualified=isinstance(node.func,ast.Attribute) and node.func.attr=='ScenarioExtension'
  if not (direct or qualified): continue
  kw={x.arg:x.value for x in node.keywords}; ms=kw.get('milestones'); edges=kw.get('milestone_edge_list')
  milestone_count=len(ms.elts) if isinstance(ms,(ast.List,ast.Tuple)) else 0 if ms is None or (isinstance(ms,ast.Constant) and ms.value is None) else None
  edge_count=len(edges.elts) if isinstance(edges,(ast.List,ast.Tuple)) else None
  out.append({'source':str(path.relative_to(REPO)),'line':node.lineno,'milestones':milestone_count,'explicit_edges':edge_count,'default_linked_list':edge_count is None})
 return out
def main():
 canonical=[x for name in MODULES for x in calls(SCENARIOS/name)]; assert len(canonical)==129 and all(x['milestones'] is not None for x in canonical)
 variants=8; milestone_dist=Counter(x['milestones'] for x in canonical); edge_mode=Counter('default_linked_list' if x['default_linked_list'] else 'explicit_dag' for x in canonical)
 files=list(REPO.rglob('*')); artifact=[p for p in files if p.is_file() and (('trajector' in p.name.lower()) or ('execution_context' in p.name.lower())) and p.suffix.lower() in ('.json','.jsonl')]
 assert not artifact
 OUT.mkdir(parents=True,exist_ok=True); (OUT/'toolsandbox_r3v2_candidates.jsonl').write_text('')
 result={
  'phase':'4.4','scientific_role':'R3_CROSS_BENCHMARK_CALIBRATION_TARGET','gate':'TOOLSANDBOX_R3V2_NO_GO',
  'repository_url':'https://github.com/apple/ToolSandbox','repository_commit':'165848b9a78cead7ca7fe7c89c688b58e6501219','package_version':'0.0.1','r3v2_specification_sha256':fsha(SPEC),
  'environment':{'python':sys.version,'platform':platform.platform()},
  'canonical_semantic_scenarios':129,'expanded_scenario_variants':1032,'augmentation_variants_per_scenario':8,
  'canonical_milestone_count_distribution':dict(milestone_dist),'expanded_scenarios_with_at_least_2_milestones':sum(v for k,v in milestone_dist.items() if k>=2)*variants,
  'canonical_scenarios_with_at_least_2_milestones':sum(v for k,v in milestone_dist.items() if k>=2),'dag_mode_distribution_canonical':dict(edge_mode),
  'official_reference_execution_available':False,'deterministically_reconstructed_reference_available':False,'reference_execution_classification':'NO_REFERENCE_EXECUTION',
  'original_replays_attempted':0,'original_replays_successful':0,'policy_llm_required_to_obtain_rollout_under_official_play_path':True,
  'scenarios_with_at_least_2_user_grounded_independent_effects':0,'scenarios_after_workflow_milestone_exclusion':0,'scenarios_with_stable_predicates':0,'scenarios_with_reference_execution':0,'scenarios_with_isolatable_producer':0,
  'final_potential_r3v2_candidate_tasks':0,'final_omission_effect_candidates':0,'category_distribution':{},'effect_type_distribution':{},'conjunct_distribution':{},
  'rejections':{'SINGLE_REQUIREMENT_TASK':(milestone_dist[0]+milestone_dist[1])*variants,'WORKFLOW_MILESTONE_NOT_USER_REQUIREMENT':0,'REQUIREMENT_GROUNDING_AMBIGUOUS':0,'EFFECT_ALIAS_OR_NON_INDEPENDENCE':0,'MILESTONE_DOWNSTREAM_DEPENDENCY':0,'NO_REFERENCE_EXECUTION':sum(v for k,v in milestone_dist.items() if k>=2)*variants,'ORIGINAL_EFFECT_WITNESS_FAILED':0,'PREDICATE_UNSTABLE':0,'NO_UNIQUE_PRODUCER':0,'OMISSION_NOT_ISOLATABLE':0,'OTHER':0},
  'evaluator':{'policy_independent_evaluation_of_supplied_context':True,'llm_judge_required':False,'deterministic_components':['exact_match','Rouge-L','database row addition/removal/update similarity','tool-trace extraction','guardrail similarity','DAG-constrained milestone mapping'],'consumes':['ExecutionContext snapshots','world databases','sandbox messages','tool traces','turn count'],'final_score':'minefield-free indicator multiplied by mean milestone similarity'},
  'reset_and_observability':{'starting_context_deep_copied_by_scenario_play':True,'execution_context_contains_world_databases_and_snapshots':True,'tool_trace_observable':True,'independent_reset_mechanically_supported':True,'r3_original_effect_witness_calibrated':False},
  'infrastructure':{'checkout_disk':'approximately 1.9 MB','python_requirement':'>=3.9','install_complexity':'Python package with pinned scientific, NLP, and multi-provider agent dependencies','external_services_for_deterministic_evaluator':'none','external_api_for_deterministic_evaluator':'none','llm_for_deterministic_evaluator':False,'llm_or_scripted_roles_for_normal_rollout':True,'reference_replay_experiment_cost':'blocked because no reference trajectory; not an infrastructure-cost blocker','cost_category':'light evaluator, heavy full agent environment'},
  'omission_trajectory_executions':0,'omission_evaluator_outcomes':0,'llm_api_calls':0,'issue_pr_search_performed':False,'candidate_sha256':fsha(OUT/'toolsandbox_r3v2_candidates.jsonl'),
 }
 write(OUT/'results.json',result)
 report=f'''# Phase 4.4 - ToolSandbox R3v2 Feasibility

## Decision

`TOOLSANDBOX_R3V2_NO_GO`

ToolSandbox is evaluated only as an `R3_CROSS_BENCHMARK_CALIBRATION_TARGET`.

## Version

- Repository: `https://github.com/apple/ToolSandbox`
- Commit: `165848b9a78cead7ca7fe7c89c688b58e6501219`
- Package: `tool_sandbox==0.0.1`
- R3v2 specification SHA-256: `{fsha(SPEC)}`

## Official representation and pipeline

`Scenario` owns a starting `ExecutionContext` and `Evaluation`. `Scenario.play()` deep-copies the starting context, then repeatedly invokes supplied user, agent, and execution-environment roles. `play_and_evaluate()` passes the resulting context to `Evaluation.evaluate()`.

`ScenarioExtension` declares `Milestone` objects and an optional edge list. `MilestoneMatcher` treats list positions as DAG node IDs; an omitted edge list becomes a linked list. `SnapshotConstraint` identifies a database namespace, target dataframe, similarity function, column measures, and optional reference milestone. Milestones can constrain persistent databases, sandbox messages, and extracted tool traces. They are evaluator predicates, not executable producer actions.

The evaluator is deterministic for an already supplied `ExecutionContext`: exact matching, Rouge-L, database row-change measures, tool-trace extraction, guardrails, and DAG-constrained matching require no LLM judge. The normal `Scenario.play()` path nevertheless needs role implementations to generate the trajectory.

## Population

- Canonical scenario extensions: 129
- Expanded tool/augmentation variants: 1,032
- Canonical scenarios with at least two milestones: 98
- Expanded variants with at least two milestones: 784
- Canonical milestone distribution: `{dict(milestone_dist)}`
- Canonical DAG modes: `{dict(edge_mode)}`

## Reference-execution blocker

The repository exposes initial contexts, milestone DAGs, predicates, tests, and an illustrative model-generated trajectory in documentation. It does not expose a corpus-level official successful tool-call trajectory, scripted successful role, execution-context fixture, or deterministic procedure that recovers producer calls and arguments from milestones.

Consequently milestones cannot establish the R3v2 chain `reference producer -> removable interaction -> original effect witness`. Treating milestone target data as a trajectory would require semantic synthesis and would violate the frozen no-policy-LLM baseline rule.

- ORIGINAL reference replay attempted: 0
- Stable R3v2 predicates calibrated against a known-valid original: 0
- Isolatable reference producers identified: 0
- Final candidates: 0 tasks / 0 omission effects

All 784 multi-milestone expanded variants are rejected as `NO_REFERENCE_EXECUTION`; the remaining 248 variants have fewer than two milestones. This does not claim that milestones are all user requirements.

## Infrastructure

The checkout is approximately 1.9 MB. The package requires Python >=3.9 and pins a broad scientific/NLP/provider stack. Evaluation of an existing context is local and requires neither an external service nor an LLM judge. Normal rollout requires policy/user roles and commonly provider dependencies. The blocker for R3v2 is missing reference execution, not evaluator cost.

## Contamination statement

No omission trajectory/evaluator, LLM API, issue/PR search, defect report, or evaluator-weakness-based candidate selection was used.
'''
 (ROOT/'PHASE4_4_TOOLSANDBOX_R3V2_FEASIBILITY.md').write_text(report)
 print(json.dumps(result,sort_keys=True))
if __name__=='__main__': main()
