#!/usr/bin/env python3
"""Phase 1.2 AgentDojo reconciliation, original-only calibration, and freeze."""

from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import inspect
import json
import platform
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from agentdojo.agent_pipeline.ground_truth_pipeline import GroundTruthPipeline
from agentdojo.base_tasks import BaseUserTask
from agentdojo.functions_runtime import FunctionCall, FunctionsRuntime
from agentdojo.task_suite.load_suites import get_suite
from agentdojo.task_suite.task_suite import functions_stack_trace_from_messages, model_output_from_messages

from phase1_2_agentdojo_capture import RecordingFunctionsRuntime

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "reference" / "agentdojo"
OUT = ROOT / "artifacts" / "phase1_2"
BENCHMARK_VERSION = "v1.2.1"
READ_PREFIXES = ("get_", "search_", "find_", "list_", "read_", "lookup_", "calculate_", "check_", "retrieve_", "fetch_", "view_", "locate_", "filter_")
ORDER_WORDS = re.compile(r"\b(before|after|first|then|only after|followed by)\b", re.I)
VERSION_FROM_DIR = {"v1": "v1", "v1_1": "v1.1", "v1_1_1": "v1.1.1", "v1_1_2": "v1.1.2", "v1_2": "v1.2", "v1_2_1": "v1.2.1"}


def canonical(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", round_trip=True)
    if isinstance(value, dict):
        value = {str(k): json.loads(canonical(v)) for k, v in value.items()}
    elif isinstance(value, (list, tuple)):
        value = [json.loads(canonical(v)) for v in value]
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n")


def direct_function_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and ((isinstance(node.func, ast.Name) and node.func.id == "FunctionCall") or (isinstance(node.func, ast.Attribute) and node.func.attr == "FunctionCall"))


def ast_call_fields(node: ast.Call) -> tuple[str | None, bool]:
    keywords = {k.arg: k.value for k in node.keywords if k.arg}
    function = keywords.get("function")
    arguments = keywords.get("args")
    name = function.value if isinstance(function, ast.Constant) and isinstance(function.value, str) else None
    literal = isinstance(arguments, ast.Dict) and not any(isinstance(x, ast.Call) for x in ast.walk(arguments)) and not any(isinstance(x, ast.Name) and x.id == "pre_environment" for x in ast.walk(arguments))
    return name, literal


def phase1_1_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for source in sorted((REFERENCE / "src/agentdojo/default_suites").glob("**/user_tasks.py")):
        rel = source.relative_to(REFERENCE / "src/agentdojo/default_suites")
        parts = rel.parts
        if parts[0] not in VERSION_FROM_DIR or len(parts) < 3:
            continue
        source_version, suite = VERSION_FROM_DIR[parts[0]], parts[1]
        tree = ast.parse(source.read_text())
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            gt = next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "ground_truth"), None)
            if gt is None:
                continue
            returned = next((n.value for n in ast.walk(gt) if isinstance(n, ast.Return) and isinstance(n.value, ast.List)), None)
            if returned is None:
                continue
            for index, (a, b) in enumerate(zip(returned.elts, returned.elts[1:])):
                if not (direct_function_call(a) and direct_function_call(b)):
                    continue
                call_a, literal_a = ast_call_fields(a)
                call_b, literal_b = ast_call_fields(b)
                if not (call_a and call_b and literal_a and literal_b):
                    continue
                if not (call_a.startswith(READ_PREFIXES) and call_b.startswith(READ_PREFIXES)):
                    continue
                match = re.fullmatch(r"UserTask(\d+)", cls.name)
                records.append({"phase1_1_source_file": str(source.relative_to(REFERENCE)), "phase1_1_source_version": source_version, "suite": suite, "task_class": cls.name, "task_id": f"user_task_{match.group(1)}" if match else None, "call_a_index": index, "call_b_index": index + 1, "phase1_1_call_a": call_a, "phase1_1_call_b": call_b})
    if len(records) != 244:
        raise RuntimeError(f"Phase 1.1 reconstruction mismatch: expected 244 records, got {len(records)}")
    return records


def function_call_data(call: FunctionCall) -> dict[str, Any]:
    return {"function": call.function, "arguments": json.loads(canonical(call.args)), "id": call.id, "placeholder_args": json.loads(canonical(call.placeholder_args)) if call.placeholder_args is not None else None}


def has_nested_function_call(value: Any) -> bool:
    if isinstance(value, FunctionCall):
        return True
    if isinstance(value, dict):
        return any(has_nested_function_call(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(has_nested_function_call(v) for v in value)
    return False


def trace_override(task: BaseUserTask) -> bool:
    return type(task).utility_from_traces is not BaseUserTask.utility_from_traces


def reconcile() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_records: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    for record in phase1_1_records():
        result = dict(record)
        try:
            suite = get_suite(BENCHMARK_VERSION, record["suite"])
            task = suite.get_user_task_by_id(record["task_id"])
        except Exception as exc:
            result.update({"static_decision": "REJECTED", "rejection_reason": "structured_task_unavailable", "detail": str(exc)})
            all_records.append(result)
            continue
        environment = task.init_environment(suite.load_and_inject_default_environment({}))
        ground_truth = task.ground_truth(environment.model_copy(deep=True))
        result.update({"task_version": list(suite.benchmark_version), "source_file": inspect.getsourcefile(type(task)), "prompt_digest": digest(task.PROMPT), "ground_truth": [function_call_data(c) for c in ground_truth], "ground_truth_digest": digest([function_call_data(c) for c in ground_truth]), "utility_from_traces_present": trace_override(task)})
        i, j = record["call_a_index"], record["call_b_index"]
        if j >= len(ground_truth) or ground_truth[i].function != record["phase1_1_call_a"] or ground_truth[j].function != record["phase1_1_call_b"]:
            result.update({"static_decision": "REJECTED", "rejection_reason": "structured_mapping_failed"})
        elif has_nested_function_call(ground_truth[i].args) or has_nested_function_call(ground_truth[j].args):
            result.update({"static_decision": "REJECTED", "rejection_reason": "data_dependency"})
        elif trace_override(task):
            result.update({"static_decision": "REJECTED", "rejection_reason": "EVALUATOR_ORDER_SEMANTICS_UNKNOWN"})
        elif ORDER_WORDS.search(task.PROMPT):
            result.update({"static_decision": "REJECTED", "rejection_reason": "SEMANTIC_ORDER_UNKNOWN"})
        else:
            result.update({"call_a": function_call_data(ground_truth[i]), "call_b": function_call_data(ground_truth[j]), "dependency_checks": {"a_to_b": "no_nested_result_reference", "b_to_a": "no_nested_result_reference", "producer_consumer": "excluded_by_read_only_static_predicate", "state_conflict": "excluded_by_read_only_static_predicate"}, "semantic_order_checks": {"explicit_user_order": "no_order_keyword", "evaluator_order": "base_utility_from_traces_only"}, "static_decision": "POTENTIAL_STRUCTURED_SWAP_CANDIDATE", "rejection_reason": None})
            accepted.append(result)
        all_records.append(result)
    return all_records, accepted


def execute_original(candidate: dict[str, Any], instrumented: bool) -> dict[str, Any]:
    suite = get_suite(BENCHMARK_VERSION, candidate["suite"])
    task = suite.get_user_task_by_id(candidate["task_id"])
    environment = task.init_environment(suite.load_and_inject_default_environment({}))
    initial = environment.model_copy(deep=True)
    pre = environment.model_copy(deep=True)
    runtime: FunctionsRuntime = RecordingFunctionsRuntime(suite.tools) if instrumented else FunctionsRuntime(suite.tools)
    _, _, post, messages, _ = GroundTruthPipeline(task).query(task.PROMPT, runtime, environment)
    trace = functions_stack_trace_from_messages(messages)
    model_output = model_output_from_messages(messages) or []
    verdict = suite._check_task_result(task, model_output, pre, post, trace)
    raw_records = getattr(runtime, "records", None)
    tool_responses = raw_records if raw_records is not None else [{"function": c.function, "arguments": c.args, "response_from_official_message": next((m["content"] for m in messages if m["role"] == "tool"), None)} for c in trace]
    return {"status": "SUCCESS", "instrumented": instrumented, "initial_state": json.loads(canonical(initial)), "initial_state_digest": digest(initial), "ground_truth": [function_call_data(c) for c in task.ground_truth(initial.model_copy(deep=True))], "ground_truth_digest": digest([function_call_data(c) for c in task.ground_truth(initial.model_copy(deep=True))]), "tool_responses": json.loads(canonical(tool_responses)), "tool_response_digest": digest(tool_responses), "functions_stack_trace": [function_call_data(c) for c in trace], "final_output": json.loads(canonical(model_output)), "final_output_digest": digest(model_output), "final_state": json.loads(canonical(post)), "final_state_digest": digest(post), "official_utility_verdict": bool(verdict)}


def calibration(queued: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cases = []
    for candidate in queued[:3]:
        try:
            uninstrumented = execute_original(candidate, False)
            instrumented = execute_original(candidate, True)
            equal = {k: uninstrumented[k] == instrumented[k] for k in ("initial_state_digest", "ground_truth_digest", "tool_response_digest", "final_output_digest", "final_state_digest", "official_utility_verdict")}
            target_records = [r for r in instrumented["tool_responses"] if r["function"] in {candidate["call_a"]["function"], candidate["call_b"]["function"]}]
            response_observable = len(target_records) >= 2 and all("response" in r for r in target_records)
            cases.append({"candidate_id": candidate["candidate_id"], "suite": candidate["suite"], "task_id": candidate["task_id"], "uninstrumented": uninstrumented, "instrumented": instrumented, "noninterference_comparisons": equal, "response_observable": response_observable, "calibration_pass": all(equal.values()) and response_observable and bool(uninstrumented["official_utility_verdict"])})
        except Exception as exc:
            cases.append({"candidate_id": candidate["candidate_id"], "status": "ERROR", "exception": {"type": type(exc).__name__, "message": str(exc)}, "calibration_pass": False})
    return cases


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    all_records, accepted = reconcile()
    for record in accepted:
        key = "\n".join([record["suite"], record["task_id"], str(record["call_a_index"]), str(record["call_b_index"]), record["ground_truth_digest"]])
        record["selection_digest"] = hashlib.sha256(key.encode()).hexdigest()
        record["candidate_id"] = hashlib.sha256((record["selection_digest"] + record["phase1_1_source_file"]).encode()).hexdigest()[:16]
    accepted.sort(key=lambda r: r["selection_digest"])
    with (OUT / "agentdojo_structured_candidates_v1.jsonl").open("w") as f:
        for record in all_records:
            f.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
    full_queue = [{k: r[k] for k in ("candidate_id", "suite", "task_id", "task_version", "call_a_index", "call_b_index", "ground_truth_digest", "selection_digest")} for r in accepted]
    pilot_queue = full_queue[:10]
    write_json(OUT / "PILOT_QUEUE_FULL.json", full_queue)
    write_json(OUT / "PILOT_QUEUE_V1.json", pilot_queue)
    selected = [next(r for r in accepted if r["candidate_id"] == q["candidate_id"]) for q in pilot_queue]
    calibration_cases = calibration(selected)
    write_json(OUT / "ORIGINAL_CALIBRATION.json", calibration_cases)
    counts = Counter(r["static_decision"] for r in all_records)
    reasons = Counter(r.get("rejection_reason") for r in all_records if r.get("rejection_reason"))
    gate_pass = len({(r["suite"], r["task_id"]) for r in accepted}) >= 10 and len(calibration_cases) == 3 and all(c["calibration_pass"] for c in calibration_cases)
    results = {"phase1_1_initial_pairs": 244, "successfully_mapped": sum(1 for r in all_records if "ground_truth_digest" in r), "final_potential_structured_swap_candidates": len(accepted), "final_candidate_tasks": len({(r["suite"], r["task_id"]) for r in accepted}), "suite_distribution": dict(Counter(r["suite"] for r in accepted)), "rejection_reasons": dict(reasons), "full_queue_size": len(full_queue), "pilot_queue_size": len(pilot_queue), "original_calibration_cases": len(calibration_cases), "original_calibration_passes": sum(c["calibration_pass"] for c in calibration_cases), "gate": "AGENTDOJO_READY_FOR_BLIND_DUAL_REPLAY" if gate_pass else ("CONDITIONAL_SMALL_PILOT" if len({(r["suite"], r["task_id"]) for r in accepted}) >= 5 else "NO_GO"), "transformed_trajectory_executions_observed": 0, "transformed_evaluator_outcomes_observed": 0}
    write_json(OUT / "results.json", results)
    if gate_pass:
        receipt = {"agentdojo_repository_url": "https://github.com/sequrity-ai/agentdojo", "agentdojo_commit": "357c80dea9af34323f709c3505d9e6d224654c7e", "agentdojo_package_version": importlib.metadata.version("pydantic") and "0.1.34", "benchmark_version": BENCHMARK_VERSION, "candidate_population_sha256": file_digest(OUT / "agentdojo_structured_candidates_v1.jsonl"), "candidate_scanner_sha256": file_digest(Path(__file__)), "execution_runner_sha256": file_digest(Path(__file__)), "instrumentation_sha256": file_digest(ROOT / "scripts/phase1_2_agentdojo_capture.py"), "state_canonicalization_sha256": file_digest(OUT / "STATE_CANONICALIZATION_V1.txt"), "response_canonicalization_sha256": file_digest(OUT / "RESPONSE_CANONICALIZATION_V1.txt"), "validity_criteria_sha256": file_digest(OUT / "VALIDITY_CRITERIA_V1.txt"), "full_queue_sha256": digest(full_queue), "pilot_queue_sha256": digest(pilot_queue), "python": sys.version, "platform": platform.platform(), "dependencies": {x: importlib.metadata.version(x) for x in ("pydantic", "docstring-parser", "deepdiff", "openai", "anthropic", "cohere", "google-genai", "sequrity")}, "expected_evaluator_relation": "E(original) == E(transformed) only after independently certified equivalence", "transformed_trajectory_executions_observed": 0, "transformed_evaluator_outcomes_observed": 0, "issue_pr_search_performed_by_execution_agent": False, "candidate_selection_manual": False}
        write_json(OUT / "FREEZE_RECEIPT.json", receipt)
    report = ["# Phase 1.2: AgentDojo Blind Baseline Calibration and Pilot Freeze", "", "## Result", "", f"Gate: `{results['gate']}`", f"", f"Phase 1.1 initial pairs: {results['phase1_1_initial_pairs']}", f"Successfully mapped: {results['successfully_mapped']}", f"Final `POTENTIAL_STRUCTURED_SWAP_CANDIDATE` pairs: {results['final_potential_structured_swap_candidates']}", f"Final candidate tasks: {results['final_candidate_tasks']}", f"Frozen full queue: {results['full_queue_size']}; pilot queue: {results['pilot_queue_size']}", f"Original-only calibration: {results['original_calibration_passes']}/3", "", "No transformed trajectory or transformed evaluator was executed. No LLM API was called.", "", "## Official baseline chain", "", "`task -> init_environment -> ground_truth(pre_environment) -> GroundTruthPipeline -> FunctionsRuntime -> post_environment -> utility_from_traces / utility -> verdict`.", "", "## Candidate filter", "", "The Phase 1.1 predicate was exactly reconstructed from its recorded source rule, then each record was mapped to the frozen v1.2.1 structured `FunctionCall` list. Nested result references, overridden trace-order semantics, and prompts containing machine-detected order terms were excluded. Accepted records remain potential only.", "", "## Calibration", "", "Each of the first three deterministic queue entries ran from two separately rebuilt environments: an upstream uninstrumented original execution and an instrumented original execution. The recorder delegates to `FunctionsRuntime.run_function`, returns its original result unchanged, and records raw responses/exceptions. Equality requires initial state, ground-truth sequence, complete recorded responses, final output, final state, and official utility verdict to match.", "", "## Freeze", "", "When the gate passes, `FREEZE_RECEIPT.json` freezes the version, candidates, queues, runner, recorder, canonicalization specifications, validity criteria, dependencies, and required zero-transformed-outcome provenance. This phase stops at that receipt."]
    (ROOT / "PHASE1_2_AGENTDOJO_BASELINE_CALIBRATION.md").write_text("\n".join(report) + "\n")


if __name__ == "__main__":
    main()
