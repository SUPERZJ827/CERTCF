#!/usr/bin/env python3
"""Execute all 184 frozen AgentDojo Phase 1.4 candidate pairs."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import phase1_3_agentdojo_run as protocol
from phase1_3_agentdojo_common import ROOT, file_digest, write_json
from phase1_3_retry1_agentdojo_run import retry1_evaluate

OUT = ROOT / "artifacts" / "phase1_4"


def verify_freeze() -> list[dict]:
    receipt = json.loads((OUT / "FREEZE_RECEIPT.json").read_text())
    paths = {
        "candidate_population_sha256": OUT / "POPULATION_V1.json",
        "population_identity_check_sha256": OUT / "POPULATION_IDENTITY_CHECK.json",
        "execution_runner_sha256": Path(__file__),
        "adapter_sha256": ROOT / "scripts/phase1_3_retry1_adapter.py",
        "instrumentation_sha256": ROOT / "scripts/phase1_3_agentdojo_common.py",
        "transformation_specification_sha256": OUT / "TRANSFORMATION_SPECIFICATION_V1.txt",
        "validity_specification_sha256": OUT / "VALIDITY_CERTIFICATE_SPECIFICATION_V1.txt",
        "evaluator_relation_specification_sha256": OUT / "EVALUATOR_RELATION_SPECIFICATION_V1.txt",
        "interpretation_specification_sha256": OUT / "INTERPRETATION_SPECIFICATION_V1.txt",
    }
    for field, path in paths.items():
        if file_digest(path) != receipt[field]:
            raise RuntimeError(f"Phase 1.4 freeze verification failed: {field}")
    identity = json.loads((OUT / "POPULATION_IDENTITY_CHECK.json").read_text())
    if identity.get("equal") is not True or identity.get("record_count") != 184 or identity.get("task_count") != 26:
        raise RuntimeError("Phase 1.4 population identity check failed")
    population = json.loads((OUT / "POPULATION_V1.json").read_text())
    if len(population) != 184:
        raise RuntimeError("Phase 1.4 population count changed")
    return population


def load_case(record: dict) -> dict:
    base = OUT / "cases" / record["candidate_id"]
    metadata = json.loads((base / "metadata.json").read_text())
    transformation = json.loads((base / "transformation_certificate.json").read_text())
    validity = json.loads((base / "validity_certificate.json").read_text())
    original_execution = json.loads((base / "original/execution.json").read_text()) if (base / "original/execution.json").exists() else None
    original_evaluator = json.loads((base / "original/evaluator.json").read_text()) if (base / "original/evaluator.json").exists() else None
    transformed_execution = json.loads((base / "transformed/execution.json").read_text()) if (base / "transformed/execution.json").exists() else None
    transformed_evaluator = json.loads((base / "transformed/evaluator.json").read_text()) if (base / "transformed/evaluator.json").exists() else None
    return {"record": record, "metadata": metadata, "transformation": transformation, "validity": validity, "original_execution": original_execution, "original_evaluator": original_evaluator, "transformed_execution": transformed_execution, "transformed_evaluator": transformed_evaluator}


def increment(bucket: dict, key: str, amount: int = 1) -> None:
    bucket[key] = bucket.get(key, 0) + amount


def aggregate(population: list[dict]) -> dict:
    cases = [load_case(record) for record in population]
    validity_counts = Counter(case["validity"]["classification"] for case in cases)
    relation_counts = Counter(case["transformed_evaluator"].get("consistency_classification") for case in cases if case["transformed_evaluator"])
    failure_taxonomy = Counter()
    by_suite: dict[str, dict] = defaultdict(dict)
    by_path: dict[str, dict] = defaultdict(dict)
    per_task: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for case in cases:
        record, validity = case["record"], case["validity"]
        suite_bucket = by_suite[record["suite"]]
        increment(suite_bucket, "population_pairs")
        per_task[(record["suite"], record["task_id"])].append(case)
        classification = validity["classification"]
        increment(suite_bucket, classification)
        relation = case["transformed_evaluator"].get("consistency_classification") if case["transformed_evaluator"] else None
        if relation:
            increment(suite_bucket, relation)
        evaluator_path = case["original_evaluator"].get("official_path_used", "UNKNOWN") if case["original_evaluator"] else "NOT_EVALUATED"
        path_bucket = by_path[evaluator_path]
        increment(path_bucket, "population_pairs")
        increment(path_bucket, classification)
        if relation:
            increment(path_bucket, relation)

        reason = validity.get("reason")
        if reason in ("BASELINE_REPLAY_FAILED", "TRANSFORMATION_CONSTRUCTION_FAILED", "INITIAL_STATE_MISMATCH"):
            failure_taxonomy[reason] += 1
        checks = validity.get("checks", {})
        if classification == "VALIDITY_FAILED":
            if checks.get("target_a_response_equal") is False or checks.get("target_b_response_equal") is False:
                failure_taxonomy["response_mismatch"] += 1
            if checks.get("complete_response_trace_equal_after_alignment") is False and checks.get("target_a_response_equal") is True and checks.get("target_b_response_equal") is True:
                failure_taxonomy["downstream_response_mismatch"] += 1
            if checks.get("final_state_equal") is False:
                failure_taxonomy["final_state_mismatch"] += 1
            if checks.get("final_output_equal") is False:
                failure_taxonomy["final_output_mismatch"] += 1
        if classification == "VALIDITY_UNKNOWN" and reason not in ("BASELINE_REPLAY_FAILED", "TRANSFORMATION_CONSTRUCTION_FAILED", "INITIAL_STATE_MISMATCH"):
            failure_taxonomy[f"other_UNKNOWN:{reason}"] += 1

    tasks_with_certified = sum(any(c["validity"]["classification"] == "VALIDITY_CERTIFIED" for c in task_cases) for task_cases in per_task.values())
    tasks_all_uncertified = sum(all(c["validity"]["classification"] != "VALIDITY_CERTIFIED" for c in task_cases) for task_cases in per_task.values())
    tasks_with_violation = sum(any(c["transformed_evaluator"] and c["transformed_evaluator"].get("consistency_classification") == "EVALUATOR_VIOLATION_CANDIDATE" for c in task_cases) for task_cases in per_task.values())
    tasks_only_invariant = sum(any(c["validity"]["classification"] == "VALIDITY_CERTIFIED" for c in task_cases) and all(not c["transformed_evaluator"] or c["transformed_evaluator"].get("consistency_classification") == "INVARIANT" for c in task_cases) for task_cases in per_task.values())

    pair_count_groups: dict[int, dict] = defaultdict(dict)
    for task_cases in per_task.values():
        bucket = pair_count_groups[len(task_cases)]
        increment(bucket, "tasks")
        increment(bucket, "population_pairs", len(task_cases))
        increment(bucket, "VALIDITY_CERTIFIED", sum(c["validity"]["classification"] == "VALIDITY_CERTIFIED" for c in task_cases))
        increment(bucket, "INVARIANT", sum(c["transformed_evaluator"] and c["transformed_evaluator"].get("consistency_classification") == "INVARIANT" for c in task_cases))
        increment(bucket, "EVALUATOR_VIOLATION_CANDIDATE", sum(c["transformed_evaluator"] and c["transformed_evaluator"].get("consistency_classification") == "EVALUATOR_VIOLATION_CANDIDATE" for c in task_cases))

    certified = validity_counts["VALIDITY_CERTIFIED"]
    violations = relation_counts["EVALUATOR_VIOLATION_CANDIDATE"]
    coverage = certified / len(cases)
    if violations >= 1:
        decision = "FULL_POPULATION_DISCOVERY_SIGNAL_PRESENT"
    elif coverage >= 0.80 and relation_counts["INVARIANT"] == certified:
        decision = "FULL_POPULATION_NO_VIOLATION_FOR_RELATION_R1"
    else:
        decision = "FULL_POPULATION_INCONCLUSIVE"

    return {
        "pair_level": {
            "population_pairs": len(population),
            "attempted_pairs": len(cases),
            "exact_transformation_success": sum(c["transformation"]["status"] == "PASS" for c in cases),
            "baseline_replay_success": sum(c["original_execution"] and c["original_execution"]["status"] == "SUCCESS" and c["original_evaluator"] and c["original_evaluator"].get("official_verdict") is True for c in cases),
            "transformed_execution_success": sum(c["transformed_execution"] and c["transformed_execution"]["status"] == "SUCCESS" for c in cases),
            "VALIDITY_CERTIFIED": certified,
            "VALIDITY_FAILED": validity_counts["VALIDITY_FAILED"],
            "VALIDITY_UNKNOWN": validity_counts["VALIDITY_UNKNOWN"],
            "INVARIANT": relation_counts["INVARIANT"],
            "EVALUATOR_VIOLATION_CANDIDATE": violations,
            "evaluator_comparisons": sum(c["transformed_evaluator"] is not None for c in cases),
            "validity_coverage": coverage,
        },
        "task_level": {
            "population_tasks": len(per_task),
            "tasks_with_at_least_one_certified_pair": tasks_with_certified,
            "tasks_with_all_pairs_uncertified": tasks_all_uncertified,
            "tasks_with_at_least_one_evaluator_violation_candidate": tasks_with_violation,
            "tasks_with_only_invariant_certified_pairs": tasks_only_invariant,
        },
        "failure_taxonomy": dict(sorted(failure_taxonomy.items())),
        "by_suite": dict(sorted(by_suite.items())),
        "by_evaluator_path": dict(sorted(by_path.items())),
        "by_candidate_pairs_per_task": {str(key): value for key, value in sorted(pair_count_groups.items())},
        "final_decision": decision,
        "issue_pr_search_performed": False,
        "llm_api_calls": 0,
    }


def main() -> None:
    population = verify_freeze()
    protocol.OUT = OUT
    protocol.evaluate = retry1_evaluate
    for record in population:
        protocol.run_case(record)
    results = aggregate(population)
    write_json(OUT / "results.json", results)
    pairs = results["pair_level"]
    tasks = results["task_level"]
    report = f"""# Phase 1.4: AgentDojo Frozen Full-Population Replay

## Decision

`{results['final_decision']}`

## Frozen population and protocol

The population identity check matched all 184 Phase 1.2A eligible pairs across 26 tasks on suite, task ID/version, adjacent indexes, ground-truth digest, function identity, and canonical arguments. The receipt froze the population and unchanged Phase 1.3 retry1 execution, adapter, instrumentation, transformation, validity, canonicalization, and evaluator-relation specifications before Phase 1.4 execution.

## Pair-level accounting

| Metric | Count |
|---|---:|
| Population / attempted | {pairs['population_pairs']} / {pairs['attempted_pairs']} |
| Exact transformation success | {pairs['exact_transformation_success']} |
| Baseline replay success | {pairs['baseline_replay_success']} |
| Transformed execution success | {pairs['transformed_execution_success']} |
| VALIDITY_CERTIFIED | {pairs['VALIDITY_CERTIFIED']} |
| VALIDITY_FAILED | {pairs['VALIDITY_FAILED']} |
| VALIDITY_UNKNOWN | {pairs['VALIDITY_UNKNOWN']} |
| Evaluator comparisons | {pairs['evaluator_comparisons']} |
| INVARIANT | {pairs['INVARIANT']} |
| EVALUATOR_VIOLATION_CANDIDATE | {pairs['EVALUATOR_VIOLATION_CANDIDATE']} |

Validity coverage: {pairs['validity_coverage']:.6f}.

## Task-level accounting

| Metric | Count |
|---|---:|
| Population tasks | {tasks['population_tasks']} |
| Tasks with at least one certified pair | {tasks['tasks_with_at_least_one_certified_pair']} |
| Tasks with all pairs uncertified | {tasks['tasks_with_all_pairs_uncertified']} |
| Tasks with at least one violation candidate | {tasks['tasks_with_at_least_one_evaluator_violation_candidate']} |
| Tasks with only invariant certified pairs | {tasks['tasks_with_only_invariant_certified_pairs']} |

## Failure taxonomy

```json
{json.dumps(results['failure_taxonomy'], indent=2, sort_keys=True)}
```

## Stratification

### Suite

```json
{json.dumps(results['by_suite'], indent=2, sort_keys=True)}
```

### Official evaluator path

```json
{json.dumps(results['by_evaluator_path'], indent=2, sort_keys=True)}
```

### Candidate pairs per task

```json
{json.dumps(results['by_candidate_pairs_per_task'], indent=2, sort_keys=True)}
```

## Interpretation and stop

Only validity-certified pairs entered evaluator comparison. Any disagreement is labelled only `EVALUATOR_VIOLATION_CANDIDATE`. The complete frozen population has been consumed; no issue/PR search, evaluator investigation, LLM call, new transformation, or candidate expansion was performed. This phase stops here.
"""
    (ROOT / "PHASE1_4_AGENTDOJO_FULL_POPULATION_REPLAY.md").write_text(report)


if __name__ == "__main__":
    main()
