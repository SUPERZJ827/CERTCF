#!/usr/bin/env python3
"""Reporting-only recovery from the 184 persisted Phase 1.4 case bundles."""

from __future__ import annotations

import json

import phase1_4_agentdojo_run as phase
from phase1_3_agentdojo_common import ROOT, write_json

OUT = ROOT / "artifacts" / "phase1_4"


def main() -> None:
    population = json.loads((OUT / "POPULATION_V1.json").read_text())
    if len(population) != 184:
        raise RuntimeError("Persisted Phase 1.4 population is incomplete")
    required = ("metadata.json", "transformation_certificate.json", "validity_certificate.json", "original/execution.json", "original/evaluator.json")
    for record in population:
        case = OUT / "cases" / record["candidate_id"]
        missing = [name for name in required if not (case / name).exists()]
        if missing:
            raise RuntimeError(f"Incomplete persisted case {record['candidate_id']}: {missing}")

    original_load_case = phase.load_case

    def reporting_load_case(record: dict) -> dict:
        case = original_load_case(record)
        if case["transformed_execution"] is None:
            case["transformed_execution"] = False
        if case["transformed_evaluator"] is None:
            case["transformed_evaluator"] = False
        return case

    phase.OUT = OUT
    phase.load_case = reporting_load_case
    results = phase.aggregate(population)
    results["pair_level"]["evaluator_comparisons"] = sum(
        (OUT / "cases" / record["candidate_id"] / "transformed/evaluator.json").exists() for record in population
    )
    results["reporting_recovery"] = {
        "reason": "None-safe aggregation of missing transformed evaluator artifacts",
        "source": "184 persisted case bundles",
        "trajectory_reexecuted": False,
        "evaluator_reexecuted": False,
        "frozen_runner_modified": False,
    }
    write_json(OUT / "results.json", results)

    pairs = results["pair_level"]
    tasks = results["task_level"]
    report = f"""# Phase 1.4: AgentDojo Frozen Full-Population Replay

## Decision

`{results['final_decision']}`

## Frozen population and execution

The pre-execution identity check matched all 184 Phase 1.2A eligible pairs across 26 tasks on suite, task ID/version, adjacent indexes, ground-truth digest, function identity, and canonical arguments. Every pair was executed from independently reconstructed original/transformed environments under the unchanged Phase 1.3 retry1 protocol.

The frozen runner completed all case executions and evidence persistence, then encountered a reporting-only `None` aggregation error. This report and `results.json` were recovered exclusively from the 184 persisted bundles; no trajectory or evaluator was rerun and the receipt/runner were not modified.

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

## Stratification by suite

```json
{json.dumps(results['by_suite'], indent=2, sort_keys=True)}
```

## Stratification by official evaluator path

```json
{json.dumps(results['by_evaluator_path'], indent=2, sort_keys=True)}
```

## Stratification by candidate pairs per task

```json
{json.dumps(results['by_candidate_pairs_per_task'], indent=2, sort_keys=True)}
```

## Interpretation and stop

Only validity-certified pairs entered evaluator comparison. Any disagreement remains labelled only `EVALUATOR_VIOLATION_CANDIDATE`. The complete frozen population has been consumed. No issue/PR search, evaluator investigation, LLM call, new transformation, normalization, or candidate expansion was performed. This phase stops here.
"""
    (ROOT / "PHASE1_4_AGENTDOJO_FULL_POPULATION_REPLAY.md").write_text(report)


if __name__ == "__main__":
    main()
