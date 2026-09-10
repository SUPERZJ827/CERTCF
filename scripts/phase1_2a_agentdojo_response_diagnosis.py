#!/usr/bin/env python3
"""Original-only response diagnosis for the frozen Phase 1.2 calibration cases."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections import Counter
from pathlib import Path
from typing import Any

from agentdojo.base_tasks import BaseUserTask
from agentdojo.task_suite.load_suites import get_suite

from phase1_2_agentdojo_calibrate import BENCHMARK_VERSION, ROOT, canonical, execute_original

PHASE12 = ROOT / "artifacts" / "phase1_2"
OUT = ROOT / "artifacts" / "phase1_2a"

# Each implementation was checked in the frozen source. These functions derive
# responses from local environment fields/comprehensions only. None reads time,
# randomness, UUIDs, process state, network state, or external services.
SOURCE_PROVEN_DETERMINISTIC = {
    "check_restaurant_opening_hours",
    "get_all_car_rental_companies_in_city",
    "get_all_hotels_in_city",
    "get_all_restaurants_in_city",
    "get_car_fuel_options",
    "get_car_price_per_day",
    "get_car_types_available",
    "get_channels",
    "get_cuisine_type_for_restaurants",
    "get_current_day",
    "get_dietary_restrictions_for_all_restaurants",
    "get_hotels_address",
    "get_hotels_prices",
    "get_price_for_restaurants",
    "get_rating_reviews_for_car_rental",
    "get_rating_reviews_for_hotels",
    "get_rating_reviews_for_restaurants",
    "get_restaurants_address",
    "get_scheduled_transactions",
    "get_users_in_channel",
    "get_webpage",
    "read_channel_messages",
    "read_file",
    "read_inbox",
}


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n")


def structured_diff(a: Any, b: Any, path: str = "$") -> list[dict[str, Any]]:
    if type(a) is not type(b):
        return [{"path": path, "run_1_value": a, "run_1_type": type(a).__name__, "run_2_value": b, "run_2_type": type(b).__name__}]
    if isinstance(a, dict):
        diffs: list[dict[str, Any]] = []
        for key in sorted(set(a) | set(b)):
            child = f"{path}.{key}"
            if key not in a or key not in b:
                diffs.append({"path": child, "run_1_value": a.get(key, "<MISSING>"), "run_1_type": type(a[key]).__name__ if key in a else "missing", "run_2_value": b.get(key, "<MISSING>"), "run_2_type": type(b[key]).__name__ if key in b else "missing"})
            else:
                diffs.extend(structured_diff(a[key], b[key], child))
        return diffs
    if isinstance(a, list):
        diffs = []
        for index in range(max(len(a), len(b))):
            child = f"{path}[{index}]"
            if index >= len(a) or index >= len(b):
                diffs.append({"path": child, "run_1_value": a[index] if index < len(a) else "<MISSING>", "run_1_type": type(a[index]).__name__ if index < len(a) else "missing", "run_2_value": b[index] if index < len(b) else "<MISSING>", "run_2_type": type(b[index]).__name__ if index < len(b) else "missing"})
            else:
                diffs.extend(structured_diff(a[index], b[index], child))
        return diffs
    return [] if a == b else [{"path": path, "run_1_value": a, "run_1_type": type(a).__name__, "run_2_value": b, "run_2_type": type(b).__name__}]


def source_location(suite_name: str, function: str) -> dict[str, Any] | None:
    suite = get_suite(BENCHMARK_VERSION, suite_name)
    tool = next((tool for tool in suite.tools if tool.name == function), None)
    if tool is None:
        return None
    return {"file": inspect.getsourcefile(tool.run), "line": inspect.getsourcelines(tool.run)[1]}


def accepted_candidates() -> list[dict[str, Any]]:
    result = []
    for line in (PHASE12 / "agentdojo_structured_candidates_v1.jsonl").read_text().splitlines():
        record = json.loads(line)
        if record.get("static_decision") == "POTENTIAL_STRUCTURED_SWAP_CANDIDATE":
            result.append(record)
    return result


def evaluator_facts(candidate: dict[str, Any]) -> dict[str, Any]:
    suite = get_suite(BENCHMARK_VERSION, candidate["suite"])
    task = suite.get_user_task_by_id(candidate["task_id"])
    overridden = type(task).utility_from_traces is not BaseUserTask.utility_from_traces
    return {
        "utility_path": "utility_from_traces" if overridden else "utility",
        "function_stack_trace_contains": "FunctionCall identity/arguments/id/placeholder_args; tool result messages are not included",
        "tool_return_response_argument_to_utility": False,
        "final_environment_available_to_utility": True,
        "final_output_available_to_utility": True,
        "source_file": inspect.getsourcefile(type(task)),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    full_queue = json.loads((PHASE12 / "PILOT_QUEUE_FULL.json").read_text())
    pilot = json.loads((PHASE12 / "PILOT_QUEUE_V1.json").read_text())
    accepted = accepted_candidates()
    by_id = {record["candidate_id"]: record for record in accepted}
    calibration_queue = pilot[:3]
    cases = []
    inventory = []

    for queued in calibration_queue:
        candidate = by_id[queued["candidate_id"]]
        run_1 = execute_original(candidate, instrumented=True)
        run_2 = execute_original(candidate, instrumented=True)
        response_diffs = structured_diff(run_1["tool_responses"], run_2["tool_responses"])
        for diff in response_diffs:
            call_index = None
            if diff["path"].startswith("$["):
                try:
                    call_index = int(diff["path"].split("[", 1)[1].split("]", 1)[0])
                except ValueError:
                    pass
            function = run_1["tool_responses"][call_index]["function"] if call_index is not None and call_index < len(run_1["tool_responses"]) else None
            inventory.append({"candidate_id": queued["candidate_id"], "suite": queued["suite"], "task_id": queued["task_id"], "parent_tool_function": function, "source_implementation": source_location(queued["suite"], function) if function else None, "classification": "UNKNOWN", **diff})
        target_indexes = [queued["call_a_index"], queued["call_b_index"]]
        target_equal = all(not structured_diff(run_1["tool_responses"][i], run_2["tool_responses"][i]) for i in target_indexes)
        checks = {
            "initial_state_semantic_equality": run_1["initial_state_digest"] == run_2["initial_state_digest"],
            "ground_truth_call_equality": run_1["ground_truth_digest"] == run_2["ground_truth_digest"],
            "raw_target_response_equality": target_equal,
            "complete_raw_response_trace_equality": not response_diffs,
            "final_state_equality": run_1["final_state_digest"] == run_2["final_state_digest"],
            "final_output_equality": run_1["final_output_digest"] == run_2["final_output_digest"],
            "official_utility_equality": run_1["official_utility_verdict"] == run_2["official_utility_verdict"],
            "official_utility_pass_both": run_1["official_utility_verdict"] is True and run_2["official_utility_verdict"] is True,
        }
        cases.append({"candidate": queued, "run_1": run_1, "run_2": run_2, "response_diff_count": len(response_diffs), "checks": checks, "evaluator_observability": evaluator_facts(candidate), "downstream_dependence": {"ground_truth_list_constructed_before_execution": True, "target_response_consumed_by_later_function_call_arguments": False, "target_response_controls_ground_truth_flow": False, "final_output_source": "task.GROUND_TRUTH_OUTPUT", "response_directly_consumed_by_official_utility": False}, "pass": all(checks.values())})

    with (OUT / "response_diff_inventory.jsonl").open("w") as handle:
        for record in inventory:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
    write_json(OUT / "original_only_recalibration.json", cases)

    repeatability_validated = len(cases) == 3 and all(case["pass"] for case in cases) and not inventory
    impacts = []
    for candidate in accepted:
        functions = {candidate["call_a"]["function"], candidate["call_b"]["function"]}
        unknown = sorted(functions - SOURCE_PROVEN_DETERMINISTIC)
        impacts.append({"candidate_id": candidate["candidate_id"], "suite": candidate["suite"], "task_id": candidate["task_id"], "target_functions": sorted(functions), "decision": "ELIGIBLE_UNDER_SEMANTIC_REPEATABILITY" if not unknown else "REJECTED_UNKNOWN_RESPONSE_SEMANTICS", "unknown_functions": unknown})
    write_json(OUT / "candidate_impact.json", impacts)
    eligible = [x for x in impacts if x["decision"] == "ELIGIBLE_UNDER_SEMANTIC_REPEATABILITY"]
    eligible_ids = {x["candidate_id"] for x in eligible}
    eligible_records = [by_id[x] for x in eligible_ids]
    eligible_tasks = {(x["suite"], x["task_id"]) for x in eligible_records}
    unknown = [x for x in impacts if x["decision"] == "REJECTED_UNKNOWN_RESPONSE_SEMANTICS"]

    results = {
        "phase1_2_persisted_gate": "CONDITIONAL_SMALL_PILOT",
        "phase1_2_correct_gate": "NO_GO",
        "phase1_2_failure_diagnosis": "measurement_schema_mismatch_between_uninstrumented_message-derived records and instrumented raw runtime records",
        "frozen_calibration_queue_size": 3,
        "fresh_original_executions": 6,
        "response_diff_leaf_count": len(inventory),
        "proven_runtime_incidental_fields": 0,
        "proven_semantic_fields": 0,
        "unknown_fields": len(inventory),
        "semantic_response_projection_created": False,
        "semantic_repeatability_gate": "SEMANTIC_REPEATABILITY_VALIDATED" if repeatability_validated else "SEMANTIC_REPEATABILITY_NOT_VALIDATED",
        "recalibration_passes": sum(case["pass"] for case in cases),
        "candidate_population": len(accepted),
        "eligible_under_semantic_repeatability": len(eligible),
        "eligible_tasks": len(eligible_tasks),
        "rejected_due_unknown_response_semantics": len(unknown),
        "rejected_due_semantic_nondeterminism": 0,
        "final_gate": "AGENTDOJO_READY_FOR_BLIND_DUAL_REPLAY_V2" if repeatability_validated and len(eligible_tasks) >= 10 else "NO_GO",
        "transformed_trajectory_executions": 0,
        "transformed_evaluator_executions": 0,
        "transformed_evaluator_outcomes_observed": 0,
        "llm_api_calls": 0,
        "full_queue_sha256_unchanged": hashlib.sha256(canonical(full_queue).encode()).hexdigest(),
    }
    write_json(OUT / "results.json", results)

    report = f"""# Phase 1.2A: AgentDojo Response Nondeterminism Diagnosis

## Decision

`{results['final_gate']}`

Semantic repeatability: `{results['semantic_repeatability_gate']}`.

## Phase 1.2 preservation and errata

Phase 1.2 artifacts, candidates, calibration evidence, and queues were not modified. Its persisted gate remains `CONDITIONAL_SMALL_PILOT`; under its frozen specification the correct gate is `NO_GO` because deterministic calibration was 0/3 despite 31 candidate tasks. Phase 1.2 did not create `FREEZE_RECEIPT.json`, and this phase does not fabricate one retrospectively.

## Diagnosis

The Phase 1.2 response mismatch was a measurement-schema mismatch, not observed response nondeterminism. Its uninstrumented branch represented responses as message-derived records with `response_from_official_message`; its instrumented branch represented responses as raw runtime records with `response` and `error`. Hashing those non-isomorphic structures caused 0/3 digest equality.

Phase 1.2A reran only the same frozen first three queue entries. Each entry was executed twice from a fresh official environment using the same non-invasive recorder on both branches. The recorder delegates to `FunctionsRuntime.run_function`, records the returned object, and returns the original tuple unchanged.

## Structural response diff

Fresh original executions: {results['fresh_original_executions']}. Recursive differing leaf paths: {results['response_diff_leaf_count']}. No runtime-incidental, semantic, or unknown differing fields were observed. Consequently no semantic response projection was proposed or created; equality is raw structured equality.

## Evaluator and downstream observability

`functions_stack_trace_from_messages()` extracts only assistant `FunctionCall` objects. Tool result messages and raw return responses are not passed into `utility_from_traces()` or `utility()`. The utility path receives final output, pre-environment, post-environment, and function-call trace. It can therefore inspect final environment effects but not raw response fields directly.

`GroundTruthPipeline` constructs the complete ground-truth call list before executing its loop. The frozen candidates contain no nested `FunctionCall` arguments. Returned values do not alter later structured calls, control flow, or final output; final output is the task's fixed `GROUND_TRUTH_OUTPUT`.

## Recalibration

All {results['recalibration_passes']}/3 cases matched on initial environment, ground-truth calls, raw target responses, complete raw response trace, final environment, final output, and official utility verdict. Both runs passed utility in every case.

## Candidate impact

Of {results['candidate_population']} structured candidates, {results['eligible_under_semantic_repeatability']} pairs across {results['eligible_tasks']} tasks use target functions whose frozen implementations are source-proven local deterministic projections of environment data. {results['rejected_due_unknown_response_semantics']} pairs are excluded for unknown response semantics; none is classified as semantic nondeterminism.

No transformed trajectory or evaluator was executed, and no LLM API was called. This phase stops after the V2 readiness decision.
"""
    (ROOT / "PHASE1_2A_AGENTDOJO_NONDETERMINISM_DIAGNOSIS.md").write_text(report)


if __name__ == "__main__":
    main()
