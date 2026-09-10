#!/usr/bin/env python3
"""Run the frozen ten-case AgentDojo blind dual-replay pilot."""

from __future__ import annotations

import copy
import inspect
import json
from collections import Counter
from pathlib import Path
from typing import Any

from agentdojo.agent_pipeline.tool_execution import tool_result_to_str
from agentdojo.base_tasks import BaseUserTask
from agentdojo.functions_runtime import FunctionCall
from agentdojo.task_suite.load_suites import get_suite
from agentdojo.task_suite.task_suite import functions_stack_trace_from_messages, model_output_from_messages
from agentdojo.types import ChatAssistantMessage, ChatToolResultMessage, text_content_block_from_string

from phase1_3_agentdojo_common import BENCHMARK_VERSION, OUT, ROOT, RecordingFunctionsRuntime, call_data, canonical_bytes, canonical_value, digest, file_digest, write_json


def verify_freeze() -> tuple[dict, list[dict], dict[str, dict]]:
    receipt = json.loads((OUT / "FREEZE_RECEIPT.json").read_text())
    checks = {
        "eligible_candidate_population_sha256": OUT / "ELIGIBLE_CANDIDATES_V1.json",
        "representative_population_sha256": OUT / "TASK_REPRESENTATIVES_V1.json",
        "full_queue_sha256": OUT / "PILOT_QUEUE_FULL_V1.json",
        "pilot_queue_sha256": OUT / "PILOT_QUEUE_V1.json",
        "execution_runner_sha256": Path(__file__),
        "tracing_instrumentation_sha256": ROOT / "scripts/phase1_3_agentdojo_common.py",
        "transformation_specification_sha256": OUT / "TRANSFORMATION_SPECIFICATION_V1.txt",
        "validity_certificate_specification_sha256": OUT / "VALIDITY_CERTIFICATE_SPECIFICATION_V1.txt",
        "evaluator_relation_specification_sha256": OUT / "EVALUATOR_RELATION_SPECIFICATION_V1.txt",
    }
    for field, path in checks.items():
        if file_digest(path) != receipt[field]:
            raise RuntimeError(f"Freeze verification failed: {field}")
    queue = json.loads((OUT / "PILOT_QUEUE_V1.json").read_text())
    if len(queue) != 10 or len({(q["suite"], q["task_id"]) for q in queue}) != 10:
        raise RuntimeError("Pilot is not exactly ten distinct tasks")
    eligible = json.loads((OUT / "ELIGIBLE_CANDIDATES_V1.json").read_text())
    return receipt, queue, {record["candidate_id"]: record for record in eligible}


def exact_swap(original: list[FunctionCall], i: int, j: int) -> tuple[list[FunctionCall], dict[str, Any]]:
    before = [call_data(call) for call in original]
    transformed = copy.deepcopy(original)
    if j != i + 1 or j >= len(transformed):
        return transformed, {"status": "TRANSFORMATION_CONSTRUCTION_FAILED", "reason": "indexes_not_adjacent_or_out_of_range"}
    if canonical_bytes(before[i]) == canonical_bytes(before[j]):
        return transformed, {"status": "TRANSFORMATION_CONSTRUCTION_FAILED", "reason": "target_calls_canonically_identical"}
    transformed[i], transformed[j] = transformed[j], transformed[i]
    after = [call_data(call) for call in transformed]
    unaffected = all(canonical_bytes(before[k]) == canonical_bytes(after[k]) for k in range(len(before)) if k not in (i, j))
    certificate = {
        "status": "PASS" if len(before) == len(after) and Counter(map(canonical_bytes, before)) == Counter(map(canonical_bytes, after)) and canonical_bytes(after[i]) == canonical_bytes(before[j]) and canonical_bytes(after[j]) == canonical_bytes(before[i]) and unaffected else "TRANSFORMATION_CONSTRUCTION_FAILED",
        "sequence_length_unchanged": len(before) == len(after),
        "function_call_multiset_unchanged": Counter(map(canonical_bytes, before)) == Counter(map(canonical_bytes, after)),
        "position_i_is_previous_b": canonical_bytes(after[i]) == canonical_bytes(before[j]),
        "position_i_plus_1_is_previous_a": canonical_bytes(after[j]) == canonical_bytes(before[i]),
        "all_other_positions_identical": unaffected,
        "temporary_variables_added": False,
        "calls_added_deleted_or_modified": False,
    }
    return transformed, certificate


def fresh_context(candidate: dict) -> tuple[Any, Any, list[FunctionCall], Any]:
    suite = get_suite(BENCHMARK_VERSION, candidate["suite"])
    task = suite.get_user_task_by_id(candidate["task_id"])
    environment = task.init_environment(suite.load_and_inject_default_environment({}))
    initial = environment.model_copy(deep=True)
    trajectory = task.ground_truth(initial.model_copy(deep=True))
    return suite, task, trajectory, environment


def execute(suite: Any, task: Any, trajectory: list[FunctionCall], environment: Any) -> dict[str, Any]:
    runtime = RecordingFunctionsRuntime(suite.tools)
    messages = []
    try:
        for call in trajectory:
            messages.append(ChatAssistantMessage(role="assistant", tool_calls=[call], content=[text_content_block_from_string("")]))
            response, _ = runtime.run_function(environment, call.function, call.args, raise_on_error=True)
            messages.append(ChatToolResultMessage(role="tool", content=[text_content_block_from_string(tool_result_to_str(response))], tool_call=call, tool_call_id=None, error=None))
        messages.append(ChatAssistantMessage(role="assistant", content=[text_content_block_from_string(task.GROUND_TRUTH_OUTPUT)], tool_calls=None))
        status, exception = "SUCCESS", None
    except Exception as exc:
        status, exception = "ERROR", {"type": type(exc).__name__, "message": str(exc)}
    trace = functions_stack_trace_from_messages(messages)
    output = model_output_from_messages(messages) if status == "SUCCESS" else None
    return {"status": status, "exception": exception, "response_trace": runtime.records, "function_stack": [call_data(call) for call in trace], "final_state": canonical_value(environment), "final_state_digest": digest(environment), "final_output": canonical_value(output) if output is not None else None, "final_output_digest": digest(output) if output is not None else None, "messages": messages, "model_output": output}


def evaluate(suite: Any, task: Any, initial: Any, execution: dict[str, Any]) -> dict[str, Any]:
    trace = [FunctionCall.model_validate(call) for call in execution["function_stack"]]
    verdict = suite._check_task_result(task, execution["model_output"] or [], initial, suite.environment_type.model_validate(execution["final_state"]), trace)
    overridden = type(task).utility_from_traces is not BaseUserTask.utility_from_traces
    return {"official_verdict": bool(verdict), "utility_from_traces_implemented": overridden, "official_path_used": "utility_from_traces" if overridden else "utility", "state_based_utility_outcome": bool(verdict) if not overridden else None, "trace_based_utility_outcome": bool(verdict) if overridden else None, "task_utility_source_file": inspect.getsourcefile(type(task))}


def identity_key(record: dict[str, Any]) -> bytes:
    return canonical_bytes({"function": record["function"], "arguments": record["arguments"]})


def validity(candidate: dict, original: dict, transformed: dict, transformation: dict, initial_equal: bool) -> dict[str, Any]:
    if transformation["status"] != "PASS":
        return {"classification": "VALIDITY_UNKNOWN", "reason": "transformation_construction_failed"}
    if original["status"] != "SUCCESS" or transformed["status"] != "SUCCESS":
        return {"classification": "VALIDITY_UNKNOWN", "reason": "execution_incomplete"}
    i, j = candidate["call_a_index"], candidate["call_b_index"]
    target_a_key = identity_key(original["response_trace"][i])
    target_b_key = identity_key(original["response_trace"][j])
    def matches(trace: list[dict], key: bytes) -> list[dict]:
        return [record for record in trace if identity_key(record) == key]
    oa, ta = matches(original["response_trace"], target_a_key), matches(transformed["response_trace"], target_a_key)
    ob, tb = matches(original["response_trace"], target_b_key), matches(transformed["response_trace"], target_b_key)
    if not all(len(items) == 1 for items in (oa, ta, ob, tb)):
        return {"classification": "VALIDITY_UNKNOWN", "reason": "ambiguous_or_missing_target_alignment", "target_match_counts": [len(oa), len(ta), len(ob), len(tb)]}
    target_a_equal = canonical_bytes(oa[0]["response"]) == canonical_bytes(ta[0]["response"])
    target_b_equal = canonical_bytes(ob[0]["response"]) == canonical_bytes(tb[0]["response"])
    aligned = copy.deepcopy(transformed["response_trace"])
    aligned[i], aligned[j] = aligned[j], aligned[i]
    for index, record in enumerate(aligned):
        record["sequence_number"] = index
    complete_trace_equal = canonical_bytes(original["response_trace"]) == canonical_bytes(aligned)
    final_state_equal = original["final_state_digest"] == transformed["final_state_digest"]
    final_output_equal = original["final_output_digest"] == transformed["final_output_digest"]
    checks = {"initial_state_equal": initial_equal, "target_a_response_equal": target_a_equal, "target_b_response_equal": target_b_equal, "complete_response_trace_equal_after_alignment": complete_trace_equal, "final_state_equal": final_state_equal, "final_output_equal": final_output_equal, "semantic_order_checks_frozen_pass": candidate["semantic_order_checks"] == {"evaluator_order": "base_utility_from_traces_only", "explicit_user_order": "no_order_keyword"}}
    classification = "VALIDITY_CERTIFIED" if all(checks.values()) else "VALIDITY_FAILED"
    failed = [key for key, value in checks.items() if not value]
    return {"classification": classification, "reason": None if classification == "VALIDITY_CERTIFIED" else ",".join(failed), "checks": checks, "target_match_counts": [len(oa), len(ta), len(ob), len(tb)]}


def persist_branch(base: Path, trajectory: list[FunctionCall], initial: Any, execution: dict, evaluator: dict | None) -> None:
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


def run_case(candidate: dict) -> None:
    case_dir = OUT / "cases" / candidate["candidate_id"]
    if case_dir.exists():
        raise FileExistsError(f"Case artifact already exists: {candidate['candidate_id']}")
    suite_o, task_o, trajectory_o, environment_o = fresh_context(candidate)
    suite_t, task_t, trajectory_t_base, environment_t = fresh_context(candidate)
    initial_o = environment_o.model_copy(deep=True)
    initial_t = environment_t.model_copy(deep=True)
    initial_equal = digest(initial_o) == digest(initial_t)
    transformed_trajectory, transformation = exact_swap(trajectory_t_base, candidate["call_a_index"], candidate["call_b_index"])
    write_json(case_dir / "metadata.json", {"candidate_id": candidate["candidate_id"], "suite": candidate["suite"], "task_id": candidate["task_id"], "task_version": candidate["task_version"], "call_a_index": candidate["call_a_index"], "call_b_index": candidate["call_b_index"], "initial_state_equal": initial_equal, "ground_truth_digest": candidate["ground_truth_digest"]})
    write_json(case_dir / "transformation_certificate.json", transformation)
    if transformation["status"] != "PASS" or not initial_equal:
        write_json(case_dir / "validity_certificate.json", {"classification": "VALIDITY_UNKNOWN", "reason": "TRANSFORMATION_CONSTRUCTION_FAILED" if transformation["status"] != "PASS" else "INITIAL_STATE_MISMATCH"})
        return
    original = execute(suite_o, task_o, trajectory_o, environment_o)
    original_eval = evaluate(suite_o, task_o, initial_o, original) if original["status"] == "SUCCESS" else {"official_verdict": False, "reason": "execution_failed"}
    persist_branch(case_dir / "original", trajectory_o, initial_o, original, original_eval)
    if original["status"] != "SUCCESS" or not original_eval["official_verdict"]:
        write_json(case_dir / "validity_certificate.json", {"classification": "VALIDITY_UNKNOWN", "reason": "BASELINE_REPLAY_FAILED"})
        return
    transformed = execute(suite_t, task_t, transformed_trajectory, environment_t)
    cert = validity(candidate, original, transformed, transformation, initial_equal)
    write_json(case_dir / "validity_certificate.json", cert)
    transformed_eval = None
    if cert["classification"] == "VALIDITY_CERTIFIED":
        transformed_eval = evaluate(suite_t, task_t, initial_t, transformed)
        transformed_eval["consistency_classification"] = "INVARIANT" if transformed_eval["official_verdict"] == original_eval["official_verdict"] else "EVALUATOR_VIOLATION_CANDIDATE"
    persist_branch(case_dir / "transformed", transformed_trajectory, initial_t, transformed, transformed_eval)


def recompute_results(queue: list[dict]) -> dict[str, Any]:
    cases = []
    for item in queue:
        base = OUT / "cases" / item["candidate_id"]
        meta = json.loads((base / "metadata.json").read_text())
        trans = json.loads((base / "transformation_certificate.json").read_text())
        cert = json.loads((base / "validity_certificate.json").read_text())
        original_execution = json.loads((base / "original/execution.json").read_text()) if (base / "original/execution.json").exists() else None
        transformed_execution = json.loads((base / "transformed/execution.json").read_text()) if (base / "transformed/execution.json").exists() else None
        transformed_eval = json.loads((base / "transformed/evaluator.json").read_text()) if (base / "transformed/evaluator.json").exists() else None
        cases.append((meta, trans, cert, original_execution, transformed_execution, transformed_eval))
    vc = Counter(case[2]["classification"] for case in cases)
    ec = Counter(case[5].get("consistency_classification") for case in cases if case[5])
    reasons = Counter(case[2].get("reason") for case in cases if case[2].get("reason"))
    certified = vc["VALIDITY_CERTIFIED"]
    violations = ec["EVALUATOR_VIOLATION_CANDIDATE"]
    gates = []
    if certified >= 5:
        gates.append("BLIND_MECHANISM_CONFIRMED_ON_AGENTDOJO")
        gates.append("BLIND_DISCOVERY_SIGNAL_PRESENT" if violations else "BLIND_PILOT_NO_VIOLATION_SIGNAL")
    else:
        gates.append("BLIND_PILOT_INCONCLUSIVE")
    return {"eligible_population_pairs": 184, "eligible_population_tasks": 26, "representative_tasks": 26, "frozen_pilot_tasks": len(queue), "attempted": len(cases), "exact_transformation_success": sum(c[1]["status"] == "PASS" for c in cases), "original_replay_success": sum(c[3] is not None and c[3]["status"] == "SUCCESS" for c in cases), "transformed_execution_success": sum(c[4] is not None and c[4]["status"] == "SUCCESS" for c in cases), "initial_state_equality": sum(c[0]["initial_state_equal"] for c in cases), "complete_response_trace_equality": sum(c[2].get("checks", {}).get("complete_response_trace_equal_after_alignment") is True for c in cases), "final_state_equality": sum(c[2].get("checks", {}).get("final_state_equal") is True for c in cases), "final_output_equality": sum(c[2].get("checks", {}).get("final_output_equal") is True for c in cases), "VALIDITY_CERTIFIED": certified, "VALIDITY_FAILED": vc["VALIDITY_FAILED"], "VALIDITY_UNKNOWN": vc["VALIDITY_UNKNOWN"], "evaluator_comparisons": sum(c[5] is not None for c in cases), "INVARIANT": ec["INVARIANT"], "EVALUATOR_VIOLATION_CANDIDATE": violations, "exclusion_reasons": dict(reasons), "gate_decisions": gates, "llm_api_calls": 0, "issue_pr_search_by_execution_agent": False}


def main() -> None:
    _, queue, eligible = verify_freeze()
    for item in queue:
        run_case(eligible[item["candidate_id"]])
    results = recompute_results(queue)
    write_json(OUT / "results.json", results)
    rows = []
    for item in queue:
        base = OUT / "cases" / item["candidate_id"]
        cert = json.loads((base / "validity_certificate.json").read_text())
        evaluator = json.loads((base / "transformed/evaluator.json").read_text()) if (base / "transformed/evaluator.json").exists() else {}
        rows.append(f"| {item['candidate_id']} | {item['suite']} | {item['task_id']} | {cert['classification']} | {evaluator.get('consistency_classification', 'NOT_COMPARED')} | {cert.get('reason') or ''} |")
    report = "# Phase 1.3: AgentDojo Blind Dual Replay V1\n\n## Frozen protocol\n\nThe eligible population, task-diverse queue, implementation, canonicalization, transformation, validity criteria, and evaluator relation were frozen before the first transformed execution. No candidate was manually selected or reordered.\n\n## Results\n\n" + "\n".join(f"- {key}: {value}" for key, value in results.items() if key not in {"exclusion_reasons", "gate_decisions"}) + "\n\nGate: " + ", ".join(f"`{gate}`" for gate in results["gate_decisions"]) + "\n\n## Per-case evidence\n\n| Case | Suite | Task | Validity | Evaluator relation | Exclusion |\n|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n\n## Exclusions\n\n" + json.dumps(results["exclusion_reasons"], sort_keys=True) + "\n\n## Interpretation\n\nOnly validity-certified cases entered evaluator comparison. An evaluator disagreement, if present, is recorded only as `EVALUATOR_VIOLATION_CANDIDATE`. No issue/PR search, LLM API call, transformation adaptation, or population expansion was performed. The experiment stops after the frozen ten-task pilot.\n"
    (ROOT / "PHASE1_3_AGENTDOJO_BLIND_DUAL_REPLAY_V1.md").write_text(report)


if __name__ == "__main__":
    main()
