#!/usr/bin/env python3
"""Execute the frozen AgentDojo R2 calibration pilot."""

from __future__ import annotations

import copy
import inspect
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from agentdojo.base_tasks import BaseUserTask

import phase1_3_agentdojo_run as protocol
from phase1_3_agentdojo_common import ROOT, call_data, canonical_bytes, digest, file_digest, write_json
from phase1_3_retry1_agentdojo_run import retry1_evaluate

OUT = ROOT / "artifacts" / "phase2_1"


def verify_freeze() -> list[dict[str, Any]]:
    receipt = json.loads((OUT / "FREEZE_RECEIPT.json").read_text())
    checks = {
        "phase2_0_candidate_sha256": ROOT / "artifacts/phase2_0/r2_literal_candidates.jsonl",
        "mutation_rules_sha256": ROOT / "artifacts/phase2_0/R2_MUTATION_RULES_V1.json",
        "mutation_viability_implementation_sha256": ROOT / "scripts/phase2_1_agentdojo_freeze.py",
        "viability_specification_sha256": OUT / "VIABILITY_SPECIFICATION_V1.txt",
        "viable_population_sha256": OUT / "R2_VIABLE_POPULATION_V1.json",
        "full_queue_sha256": OUT / "R2_PILOT_QUEUE_FULL_V1.json",
        "pilot_queue_sha256": OUT / "R2_PILOT_QUEUE_V1.json",
        "execution_runner_sha256": Path(__file__),
        "instrumentation_sha256": ROOT / "scripts/phase1_3_agentdojo_common.py",
        "adapter_sha256": ROOT / "scripts/phase1_3_retry1_adapter.py",
        "mutation_exactness_specification_sha256": OUT / "MUTATION_EXACTNESS_SPECIFICATION_V1.txt",
        "semantic_violation_specification_sha256": OUT / "SEMANTIC_VIOLATION_SPECIFICATION_V1.txt",
        "evaluator_relation_sha256": OUT / "EVALUATOR_RELATION_SPECIFICATION_V1.txt",
        "state_canonicalization_sha256": ROOT / "artifacts/phase1_2/STATE_CANONICALIZATION_V1.txt",
        "response_canonicalization_sha256": ROOT / "artifacts/phase1_2/RESPONSE_CANONICALIZATION_V1.txt",
    }
    for field, path in checks.items():
        if file_digest(path) != receipt[field]:
            raise RuntimeError(f"Phase 2.1 freeze verification failed: {field}")
    queue = json.loads((OUT / "R2_PILOT_QUEUE_V1.json").read_text())
    if len(queue) != 10 or len({(record["suite"], record["task_id"]) for record in queue}) != 10:
        raise RuntimeError("Frozen R2 pilot is not ten distinct tasks")
    return queue


def exact_mutation(trajectory: list[Any], candidate: dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
    before = [call_data(call) for call in trajectory]
    transformed = copy.deepcopy(trajectory)
    index = candidate["call_index"]
    name = candidate["argument_name"]
    if index >= len(transformed) or name not in transformed[index].args:
        return transformed, {"status": "MUTATION_CONSTRUCTION_FAILED", "reason": "target_mapping_unavailable"}
    original_value = transformed[index].args[name]
    if canonical_bytes(original_value) != canonical_bytes(candidate["original_value"]):
        return transformed, {"status": "MUTATION_CONSTRUCTION_FAILED", "reason": "original_value_mismatch"}
    new_args = copy.deepcopy(transformed[index].args)
    new_args[name] = copy.deepcopy(candidate["proposed_value"])
    transformed[index] = transformed[index].model_copy(update={"args": new_args}, deep=True)
    after = [call_data(call) for call in transformed]
    differing_calls = [position for position, (left, right) in enumerate(zip(before, after)) if canonical_bytes(left) != canonical_bytes(right)]
    before_target = before[index]
    after_target = after[index]
    differing_arguments = [key for key in set(before_target["arguments"]) | set(after_target["arguments"]) if canonical_bytes(before_target["arguments"].get(key)) != canonical_bytes(after_target["arguments"].get(key))]
    checks = {
        "trajectory_length_unchanged": len(before) == len(after),
        "function_sequence_unchanged": [call["function"] for call in before] == [call["function"] for call in after],
        "target_call_position_unchanged": before_target["function"] == after_target["function"],
        "only_target_call_changed": differing_calls == [index],
        "exactly_one_argument_changed": differing_arguments == [name],
        "original_value_is_grounded_literal": canonical_bytes(before_target["arguments"][name]) == canonical_bytes(candidate["original_value"]),
        "transformed_value_is_frozen_replacement": canonical_bytes(after_target["arguments"][name]) == canonical_bytes(candidate["proposed_value"]),
        "replacement_differs": canonical_bytes(candidate["original_value"]) != canonical_bytes(candidate["proposed_value"]),
    }
    status = "PASS" if all(checks.values()) else "MUTATION_CONSTRUCTION_FAILED"
    return transformed, {"certificate_type": "MUTATION_EXACTNESS_CERTIFICATE", "status": status, "checks": checks, "differing_call_positions": differing_calls, "differing_arguments": differing_arguments, "target_argument_before": candidate["original_value"], "target_argument_after": candidate["proposed_value"]}


def response_only(record: dict[str, Any]) -> dict[str, Any]:
    return {"response": record.get("response"), "error": record.get("error"), "exception": record.get("exception")}


def semantic_certificate(candidate: dict[str, Any], original: dict[str, Any], transformed: dict[str, Any], exactness: dict[str, Any], initial_equal: bool) -> dict[str, Any]:
    index = candidate["call_index"]
    base_checks = {
        "unique_explicit_prompt_literal": candidate["prompt_match_count"] == 1,
        "unique_ground_truth_literal": candidate["ground_truth_literal_occurrences"] == 1,
        "original_grounding_exact": canonical_bytes(original["response_trace"][index]["arguments"][candidate["argument_name"]]) == canonical_bytes(candidate["original_value"]) if len(original["response_trace"]) > index else False,
        "transformed_uses_frozen_replacement": canonical_bytes(transformed["response_trace"][index]["arguments"][candidate["argument_name"]]) == canonical_bytes(candidate["proposed_value"]) if len(transformed["response_trace"]) > index else False,
        "replacement_differs": canonical_bytes(candidate["original_value"]) != canonical_bytes(candidate["proposed_value"]),
        "mutation_exactness": exactness["status"] == "PASS",
        "initial_state_equal": initial_equal,
        "anti_compensation_check": candidate["anti_compensation_check"] == "PASS",
        "no_schema_uncertainty": candidate["complete_modified_arguments_schema_valid"] is True,
    }
    if transformed["status"] != "SUCCESS" or len(transformed["response_trace"]) <= index or transformed["response_trace"][index].get("exception") is not None or transformed["response_trace"][index].get("error") is not None:
        return {"classification": "MUTATION_EXECUTION_INVALID", "reason": "perturbed_target_execution_failed", "checks": base_checks}
    base_checks["perturbed_target_execution_success"] = True
    target_response_differs = canonical_bytes(response_only(original["response_trace"][index])) != canonical_bytes(response_only(transformed["response_trace"][index]))
    downstream_response_differs = canonical_bytes([response_only(item) for item in original["response_trace"][index + 1 :]]) != canonical_bytes([response_only(item) for item in transformed["response_trace"][index + 1 :]])
    final_state_differs = original["final_state_digest"] != transformed["final_state_digest"]
    final_output_differs = original["final_output_digest"] != transformed["final_output_digest"]
    behavior = {"target_raw_response_differs": target_response_differs, "downstream_response_differs": downstream_response_differs, "final_environment_differs": final_state_differs, "final_output_differs": final_output_differs}
    if not all(base_checks.values()):
        return {"classification": "SEMANTIC_VIOLATION_UNKNOWN", "reason": "certificate_precondition_failed", "checks": base_checks, "observable_behavior": behavior}
    if not any(behavior.values()):
        return {"classification": "BEHAVIORALLY_EQUIVALENT_MUTATION", "reason": "no_observable_behavioral_difference", "checks": base_checks, "observable_behavior": behavior}
    return {"classification": "SEMANTIC_VIOLATION_CERTIFIED", "reason": None, "checks": base_checks, "observable_behavior": behavior, "target_argument_before": candidate["original_value"], "target_argument_after": candidate["proposed_value"]}


def persist_branch(base: Path, trajectory: list[Any], initial: Any, execution: dict[str, Any], evaluator: dict[str, Any] | None) -> None:
    write_json(base / "trajectory.json", [call_data(call) for call in trajectory])
    write_json(base / "execution.json", {"status": execution["status"], "exception": execution["exception"]})
    write_json(base / "response_trace.json", execution["response_trace"])
    write_json(base / "function_stack.json", execution["function_stack"])
    write_json(base / "initial_state.json", initial)
    (base / "initial_state.sha256").write_text(digest(initial) + "\n")
    write_json(base / "final_state.json", execution["final_state"])
    (base / "final_state.sha256").write_text(execution["final_state_digest"] + "\n")
    write_json(base / "final_output.json", execution["final_output"])
    if evaluator is not None:
        write_json(base / "evaluator.json", evaluator)


def run_case(candidate: dict[str, Any]) -> None:
    case_dir = OUT / "cases" / candidate["candidate_id"]
    if case_dir.exists():
        raise FileExistsError(case_dir)
    suite_o, task_o, trajectory_o, environment_o = protocol.fresh_context(candidate)
    suite_t, task_t, trajectory_t_base, environment_t = protocol.fresh_context(candidate)
    initial_o = environment_o.model_copy(deep=True)
    initial_t = environment_t.model_copy(deep=True)
    initial_equal = digest(initial_o) == digest(initial_t)
    transformed_trajectory, exactness = exact_mutation(trajectory_t_base, candidate)
    write_json(case_dir / "metadata.json", {"candidate_id": candidate["candidate_id"], "suite": candidate["suite"], "task_id": candidate["task_id"], "task_version": candidate["task_version"], "argument_type": candidate["argument_type"], "call_index": candidate["call_index"], "argument_name": candidate["argument_name"], "initial_state_equal": initial_equal, "ground_truth_digest": candidate["ground_truth_digest"], "prompt_digest": candidate["prompt_digest"], "mutation_rule": candidate["mutation_rule"]})
    write_json(case_dir / "mutation_exactness_certificate.json", exactness)
    if exactness["status"] != "PASS" or not initial_equal:
        write_json(case_dir / "semantic_violation_certificate.json", {"classification": "SEMANTIC_VIOLATION_UNKNOWN", "reason": "MUTATION_CONSTRUCTION_FAILED" if exactness["status"] != "PASS" else "INITIAL_STATE_MISMATCH"})
        return
    original = protocol.execute(suite_o, task_o, trajectory_o, environment_o)
    original_eval = retry1_evaluate(suite_o, task_o, initial_o, original) if original["status"] == "SUCCESS" else {"official_verdict": False, "reason": "execution_failed"}
    persist_branch(case_dir / "original", trajectory_o, initial_o, original, original_eval)
    if original["status"] != "SUCCESS" or original_eval.get("official_verdict") is not True:
        write_json(case_dir / "semantic_violation_certificate.json", {"classification": "SEMANTIC_VIOLATION_UNKNOWN", "reason": "BASELINE_REPLAY_FAILED"})
        return
    transformed = protocol.execute(suite_t, task_t, transformed_trajectory, environment_t)
    certificate = semantic_certificate(candidate, original, transformed, exactness, initial_equal)
    write_json(case_dir / "semantic_violation_certificate.json", certificate)
    transformed_eval = None
    if certificate["classification"] == "SEMANTIC_VIOLATION_CERTIFIED":
        transformed_eval = retry1_evaluate(suite_t, task_t, initial_t, transformed)
        transformed_eval["sensitivity_classification"] = "SENSITIVE" if transformed_eval["official_verdict"] is False else "EVALUATOR_FALSE_ACCEPTANCE_CANDIDATE"
    persist_branch(case_dir / "transformed", transformed_trajectory, initial_t, transformed, transformed_eval)


def aggregate(queue: list[dict[str, Any]]) -> dict[str, Any]:
    cases = []
    for candidate in queue:
        base = OUT / "cases" / candidate["candidate_id"]
        cases.append({
            "candidate": candidate,
            "exactness": json.loads((base / "mutation_exactness_certificate.json").read_text()),
            "certificate": json.loads((base / "semantic_violation_certificate.json").read_text()),
            "original_execution": json.loads((base / "original/execution.json").read_text()) if (base / "original/execution.json").exists() else None,
            "original_evaluator": json.loads((base / "original/evaluator.json").read_text()) if (base / "original/evaluator.json").exists() else None,
            "transformed_execution": json.loads((base / "transformed/execution.json").read_text()) if (base / "transformed/execution.json").exists() else None,
            "transformed_evaluator": json.loads((base / "transformed/evaluator.json").read_text()) if (base / "transformed/evaluator.json").exists() else None,
        })
    classes = Counter(case["certificate"]["classification"] for case in cases)
    relations = Counter(case["transformed_evaluator"].get("sensitivity_classification") for case in cases if case["transformed_evaluator"])
    by_type: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for case in cases:
        bucket = by_type[case["candidate"]["argument_type"]]
        bucket["attempted"] += 1
        bucket[case["certificate"]["classification"]] += 1
        if case["transformed_evaluator"]:
            bucket[case["transformed_evaluator"]["sensitivity_classification"]] += 1
    certified = classes["SEMANTIC_VIOLATION_CERTIFIED"]
    false_acceptances = relations["EVALUATOR_FALSE_ACCEPTANCE_CANDIDATE"]
    gates = []
    if certified >= 5:
        gates.append("R2_MECHANISM_VALIDATED")
        if false_acceptances:
            gates.append("R2_CALIBRATION_SIGNAL_PRESENT")
        elif relations["SENSITIVE"] == certified:
            gates.append("R2_EVALUATOR_SENSITIVE_ON_CALIBRATION_PILOT")
    else:
        gates.append("R2_CALIBRATION_INCONCLUSIVE")
    viable = json.loads((OUT / "R2_VIABLE_POPULATION_V1.json").read_text())
    return {
        "phase2_0_candidates": 104,
        "mutation_viable_candidates": len(viable),
        "mutation_viable_tasks": len({(record["suite"], record["task_id"]) for record in viable}),
        "frozen_pilot_size": len(queue),
        "attempted": len(cases),
        "baseline_replay_success": sum(case["original_execution"] is not None and case["original_execution"]["status"] == "SUCCESS" and case["original_evaluator"] and case["original_evaluator"].get("official_verdict") is True for case in cases),
        "mutation_exactness_success": sum(case["exactness"]["status"] == "PASS" for case in cases),
        "perturbed_execution_success": sum(case["transformed_execution"] is not None and case["transformed_execution"]["status"] == "SUCCESS" for case in cases),
        "SEMANTIC_VIOLATION_CERTIFIED": certified,
        "SEMANTIC_VIOLATION_UNKNOWN": classes["SEMANTIC_VIOLATION_UNKNOWN"],
        "BEHAVIORALLY_EQUIVALENT_MUTATION": classes["BEHAVIORALLY_EQUIVALENT_MUTATION"],
        "MUTATION_EXECUTION_INVALID": classes["MUTATION_EXECUTION_INVALID"],
        "evaluator_comparisons": sum(case["transformed_evaluator"] is not None for case in cases),
        "SENSITIVE": relations["SENSITIVE"],
        "EVALUATOR_FALSE_ACCEPTANCE_CANDIDATE": false_acceptances,
        "by_argument_type": {key: dict(value) for key, value in sorted(by_type.items())},
        "exclusion_reasons": dict(Counter(case["certificate"].get("reason") for case in cases if case["certificate"].get("reason"))),
        "gate_decisions": gates,
        "llm_api_calls": 0,
        "issue_pr_search_performed": False,
    }


def main() -> None:
    queue = verify_freeze()
    protocol.OUT = OUT
    protocol.evaluate = retry1_evaluate
    for candidate in queue:
        run_case(candidate)
    results = aggregate(queue)
    write_json(OUT / "results.json", results)
    rows = []
    for candidate in queue:
        base = OUT / "cases" / candidate["candidate_id"]
        certificate = json.loads((base / "semantic_violation_certificate.json").read_text())
        evaluator = json.loads((base / "transformed/evaluator.json").read_text()) if (base / "transformed/evaluator.json").exists() else {}
        rows.append(f"| {candidate['candidate_id']} | {candidate['suite']} | {candidate['task_id']} | {candidate['argument_type']} | {certificate['classification']} | {evaluator.get('sensitivity_classification', 'NOT_COMPARED')} |")
    report = f"""# Phase 2.1: AgentDojo R2 Calibration Pilot

## Role and frozen relation

AgentDojo is an R2 `CALIBRATION_TARGET`, not a blind discovery target. R2 changes exactly one uniquely prompt-grounded official GT argument to its frozen deterministic, schema-valid, source/environment-supported replacement. Only a successfully executed, behaviorally observable, uncompensated mutation can be `SEMANTIC_VIOLATION_CERTIFIED` and enter evaluator sensitivity comparison.

## Results

```json
{json.dumps(results, indent=2, sort_keys=True)}
```

Gate: {', '.join(f'`{gate}`' for gate in results['gate_decisions'])}

## Per-case evidence

| Candidate | Suite | Task | Argument type | Certificate | Evaluator relation |
|---|---|---|---|---|---|
{chr(10).join(rows)}

## Interpretation and stop

`SENSITIVE` means the official evaluator rejected a machine-certified semantic violation. A transformed pass is labelled only `EVALUATOR_FALSE_ACCEPTANCE_CANDIDATE`. The complete frozen calibration pilot is persisted. No issue/PR search, LLM API call, task/evaluator modification, candidate expansion, R3 design, or full 104-candidate execution was performed.
"""
    (ROOT / "PHASE2_1_AGENTDOJO_R2_CALIBRATION_PILOT.md").write_text(report)


if __name__ == "__main__":
    main()
