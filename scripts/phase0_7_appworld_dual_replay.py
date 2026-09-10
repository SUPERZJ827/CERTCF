#!/usr/bin/env python3
"""Frozen Phase 0.7 AppWorld independent-call dual replay pilot.

The script deliberately implements one transformation only: swap two complete,
direct API-call statements selected by the Phase 0.6 artifact.  It writes an
evidence package before invoking a transformed evaluator, and invokes that
evaluator only for validity-certified executions.
"""

from __future__ import annotations
from os import environ

import argparse
import ast
import base64
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


VALIDITY_CERTIFIED = "VALIDITY_CERTIFIED"
VALIDITY_FAILED = "VALIDITY_FAILED"
VALIDITY_UNKNOWN = "VALIDITY_UNKNOWN"


def canonical(value: Any) -> Any:
    """Make supported values deterministic without dropping any fields."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("non-finite float cannot be deterministically canonicalized")
        return value
    if isinstance(value, (date, datetime)):
        return {"__type__": type(value).__name__, "value": value.isoformat()}
    if isinstance(value, bytes):
        return {"__type__": "bytes", "base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {str(key): canonical(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    raise TypeError(f"unsupported value for canonical serialization: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(canonical(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value) + b"\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def candidate_digest(candidate: dict[str, Any]) -> str:
    return digest_bytes(f"{candidate['task_id']}{candidate['i']}{candidate['j']}".encode("utf-8"))


def select_pilot_queue(records: list[dict[str, Any]], size: int = 10) -> tuple[list[dict[str, Any]], str]:
    candidates = [record for record in records if record.get("status") == "POTENTIAL_CANDIDATE"]
    queue = []
    for record in candidates:
        selected = dict(record)
        selected["selection_digest"] = candidate_digest(record)
        queue.append(selected)
    queue.sort(key=lambda item: (item["selection_digest"], item["task_id"], item["i"], item["j"]))
    return queue[:size], digest_bytes(canonical_bytes(queue[:size]))


def file_tree_digest(directory: Path) -> tuple[str, dict[str, Any]]:
    """Digest a complete snapshot while sorting JSON object keys and JSONL rows."""
    files: dict[str, Any] = {}
    for path in sorted(item for item in directory.rglob("*") if item.is_file()):
        relative = str(path.relative_to(directory))
        raw = path.read_bytes()
        try:
            if path.suffix == ".json":
                value: Any = {"kind": "json", "value": json.loads(raw)}
            elif path.suffix == ".jsonl":
                rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
                value = {
                    "kind": "jsonl",
                    "rows": sorted((canonical(row) for row in rows), key=lambda row: canonical_bytes(row)),
                }
            else:
                value = {"kind": "bytes", "sha256": digest_bytes(raw)}
        except (UnicodeDecodeError, json.JSONDecodeError):
            value = {"kind": "bytes", "sha256": digest_bytes(raw)}
        files[relative] = value
    payload = {"files": files}
    return digest_bytes(canonical_bytes(payload)), payload


def api_path(call: ast.Call) -> str | None:
    if not isinstance(call.func, ast.Attribute):
        return None
    attrs: list[str] = []
    current: ast.AST = call.func
    while isinstance(current, ast.Attribute):
        attrs.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name) or current.id != "apis":
        return None
    return "apis." + ".".join(reversed(attrs))


@dataclass(frozen=True)
class LocatedCall:
    index: int
    call: ast.Call
    statement: ast.stmt
    statement_list: list[ast.stmt]


def locate_api_calls(source: str) -> list[LocatedCall]:
    tree = ast.parse(source)
    solution = next(
        (node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "solution"),
        None,
    )
    if solution is None:
        raise ValueError("solution() is missing")
    parents: dict[ast.AST, ast.AST] = {}
    statement_lists: dict[ast.stmt, list[ast.stmt]] = {}
    for parent in ast.walk(solution):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
        for _, value in ast.iter_fields(parent):
            if isinstance(value, list) and value and all(isinstance(item, ast.stmt) for item in value):
                for statement in value:
                    statement_lists[statement] = value
    calls = sorted(
        (node for node in ast.walk(solution) if isinstance(node, ast.Call) and api_path(node)),
        key=lambda node: (node.lineno, node.col_offset),
    )
    located: list[LocatedCall] = []
    for index, call in enumerate(calls, start=1):
        current: ast.AST = call
        while current in parents and not isinstance(current, ast.stmt):
            current = parents[current]
        if not isinstance(current, ast.stmt) or current not in statement_lists:
            raise ValueError(f"call {index} has no enclosing executable statement")
        located.append(LocatedCall(index=index, call=call, statement=current, statement_list=statement_lists[current]))
    return located


def is_direct_api_statement(located: LocatedCall) -> bool:
    statement = located.statement
    if isinstance(statement, ast.Expr):
        return statement.value is located.call
    if isinstance(statement, ast.Assign):
        return statement.value is located.call
    if isinstance(statement, ast.AnnAssign):
        return statement.value is located.call
    return False


def line_offsets(source: str) -> list[int]:
    starts = [0]
    for position, character in enumerate(source):
        if character == "\n":
            starts.append(position + 1)
    return starts


def statement_span(source: str, statement: ast.stmt) -> tuple[int, int]:
    starts = line_offsets(source)
    return starts[statement.lineno - 1] + statement.col_offset, starts[statement.end_lineno - 1] + statement.end_col_offset


def transform_swap(source: str, call_a_index: int, call_b_index: int) -> tuple[str, dict[str, Any]]:
    locations = {item.index: item for item in locate_api_calls(source)}
    if call_a_index not in locations or call_b_index not in locations:
        raise ValueError("candidate call index is absent from compiled solution")
    left, right = locations[call_a_index], locations[call_b_index]
    if left.statement is right.statement:
        raise ValueError("both calls share a statement; strict transformation is unsupported")
    if left.statement_list is not right.statement_list:
        raise ValueError("calls are not in the same executable statement block")
    if not is_direct_api_statement(left) or not is_direct_api_statement(right):
        raise ValueError("calls are not direct complete API-call statements")
    left_span, right_span = statement_span(source, left.statement), statement_span(source, right.statement)
    if left_span[0] > right_span[0]:
        left, right, left_span, right_span = right, left, right_span, left_span
    if left_span[1] > right_span[0]:
        raise ValueError("statement spans overlap")
    transformed = source[: left_span[0]] + source[right_span[0] : right_span[1]] + source[left_span[1] : right_span[0]] + source[left_span[0] : left_span[1]] + source[right_span[1] :]
    return transformed, {
        "kind": "swap_complete_direct_api_statements",
        "requested_call_indices": [call_a_index, call_b_index],
        "actual_source_order_indices": [left.index, right.index],
        "call_a": api_path(left.call),
        "call_b": api_path(right.call),
        "original_statement_spans": [list(left_span), list(right_span)],
    }


def execution_ok(message: str) -> bool:
    return not message.startswith("Execution failed.") and "Execution timed out after" not in message


def normalized_call(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Extract identity, arguments, and returned value without discarding unknown fields."""
    identity = entry.get("api_name") or entry.get("name") or entry.get("api") or entry.get("path")
    arguments = entry.get("arguments")
    if arguments is None:
        arguments = entry.get("kwargs", entry.get("request", entry.get("request_data")))
    returned = entry.get("response")
    if returned is None:
        returned = entry.get("result", entry.get("response_data", entry.get("output")))
    if identity is None or arguments is None or returned is None:
        return None
    try:
        return {"identity": str(identity).removeprefix("apis."), "arguments": canonical(arguments), "returned": canonical(returned)}
    except (TypeError, ValueError):
        return None


def target_call_multimap(entries: list[dict[str, Any]], target_identities: set[str]) -> dict[bytes, list[Any]] | None:
    selected: dict[bytes, list[Any]] = defaultdict(list)
    for entry in entries:
        item = normalized_call(entry)
        if item is None:
            return None
        if item["identity"] in target_identities:
            key = canonical_bytes({"identity": item["identity"], "arguments": item["arguments"]})
            selected[key].append(item["returned"])
    if not selected:
        return None
    for values in selected.values():
        values.sort(key=canonical_bytes)
    return dict(selected)


def classify_validity(
    *,
    original_initial_digest: str | None,
    transformed_initial_digest: str | None,
    original_execution_ok: bool,
    transformed_execution_ok: bool,
    calls_match: bool | None,
    final_state_match: bool | None,
    returned_value_match: bool | None,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if original_initial_digest is None or transformed_initial_digest is None:
        return VALIDITY_UNKNOWN, ["initial_state_unavailable"]
    if original_initial_digest != transformed_initial_digest:
        return VALIDITY_FAILED, ["initial_state_digest_mismatch"]
    if not original_execution_ok or not transformed_execution_ok:
        return VALIDITY_UNKNOWN, ["execution_error_or_timeout"]
    unknowns = []
    failures = []
    for label, result in (("target_call_trace", calls_match), ("final_state", final_state_match), ("returned_value", returned_value_match)):
        if result is None:
            unknowns.append(f"{label}_unavailable")
        elif not result:
            failures.append(f"{label}_mismatch")
    if failures:
        return VALIDITY_FAILED, failures
    if unknowns:
        return VALIDITY_UNKNOWN, unknowns
    return VALIDITY_CERTIFIED, reasons


def evaluator_relation(validity: str, original: Any, transformed: Any) -> str:
    if validity != VALIDITY_CERTIFIED:
        return "INVALID_FOR_EVALUATOR_TEST"
    return "INVARIANT" if canonical(original) == canonical(transformed) else "EVALUATOR_VIOLATION_CANDIDATE"


def evaluator_payload(world: Any) -> dict[str, Any]:
    try:
        tracker = world.evaluate(suppress_errors=True)
        raw = tracker.to_dict() if hasattr(tracker, "to_dict") else dict(tracker)
        return {"status": "COMPLETED", "result": canonical(raw)}
    except Exception as exc:  # evaluator failure is evidence, not a reason to alter a candidate
        return {"status": "ERROR", "exception_type": type(exc).__name__, "message": str(exc)}


def runtime_environment() -> dict[str, Any]:
    try:
        appworld_version = importlib.metadata.version("appworld")
    except importlib.metadata.PackageNotFoundError:
        appworld_version = None
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "appworld_version": appworld_version,
        "normalization_rules": {
            "state": "JSON keys and JSONL rows are canonicalized; no fields are omitted.",
            "api_calls": "Only the target calls are aligned by API identity plus canonical arguments; their complete returned values are compared.",
            "return_value": "Canonical JSON serialization with no evaluator-specific normalization.",
        },
    }


def run_execution(*, task_id: str, source: str, run_dir: Path, experiment_name: str) -> dict[str, Any]:
    from appworld.environment import AppWorld

    run_dir.mkdir(parents=True, exist_ok=True)
    world = AppWorld(task_id=task_id, experiment_name=experiment_name, ground_truth_mode="full")
    initial_dir = run_dir / "initial_state"
    final_dir = run_dir / "final_state"
    try:
        if world.models is None:
            raise RuntimeError("local model collection unavailable")
        world.models.save(str(initial_dir), format="full", delete_if_exists=True)
        initial_digest, _ = file_tree_digest(initial_dir)
        invocation = "\n__phase0_7_return_value = solution(apis, requester)\n"
        message = world.execute(source + invocation)
        result_marker = object()
        returned = world.shell.user_ns.get("__phase0_7_return_value", result_marker) if world.shell is not None else result_marker
        return_available = returned is not result_marker
        try:
            returned_canonical: Any = canonical(returned) if return_available else None
        except (TypeError, ValueError):
            return_available = False
            returned_canonical = None
        api_log_path = Path(world.output_logs_directory) / "api_calls.jsonl"
        api_calls = read_jsonl(api_log_path) if api_log_path.exists() else []
        world.models.save(str(final_dir), format="full", delete_if_exists=True)
        final_digest, final_state = file_tree_digest(final_dir)
        write_json(run_dir / "execution.json", {
            "execution_success": execution_ok(message), "message": message, "return_value_available": return_available,
            "return_value": returned_canonical, "api_calls": api_calls, "initial_state_digest": initial_digest,
        })
        write_json(run_dir / "final_state.json", final_state)
        (run_dir / "final_state.sha256").write_text(final_digest + "\n")
        return {
            "execution_success": execution_ok(message), "message": message, "return_value_available": return_available,
            "return_value": returned_canonical, "api_calls": api_calls, "initial_state_digest": initial_digest,
            "final_state_digest": final_digest,
        }
    except Exception as exc:
        payload = {"execution_success": False, "exception_type": type(exc).__name__, "message": str(exc)}
        write_json(run_dir / "execution.json", payload)
        return payload
    finally:
        world.close()


def case_id(candidate: dict[str, Any]) -> str:
    return f"{candidate['task_id']}__{candidate['i']}_{candidate['j']}"


def run_case(candidate: dict[str, Any], *, data_root: Path, cases_dir: Path, environment: dict[str, Any]) -> dict[str, Any]:
    identifier = case_id(candidate)
    case_dir = cases_dir / identifier
    shutil.rmtree(case_dir, ignore_errors=True)
    case_dir.mkdir(parents=True)
    source_path = data_root / "data" / "tasks" / candidate["task_id"] / "ground_truth" / "compiled_solution.py"
    original_source = source_path.read_text()
    write_json(case_dir / "metadata.json", {"candidate": candidate, "environment": environment, "source_path": str(source_path)})
    (case_dir / "original" / "solution.py").parent.mkdir(parents=True, exist_ok=True)
    (case_dir / "original" / "solution.py").write_text(original_source)
    try:
        transformed_source, transformation = transform_swap(original_source, candidate["i"], candidate["j"])
    except ValueError as exc:
        certificate = {"classification": VALIDITY_UNKNOWN, "reasons": ["transformation_precondition_failed", str(exc)]}
        write_json(case_dir / "validity_certificate.json", certificate)
        return {"case_id": identifier, "task_id": candidate["task_id"], "validity": VALIDITY_UNKNOWN, "relation": "INVALID_FOR_EVALUATOR_TEST", "reason": certificate["reasons"]}
    (case_dir / "transformed").mkdir(parents=True, exist_ok=True)
    (case_dir / "transformed" / "solution.py").write_text(transformed_source)
    transformation.update({"original_source_sha256": digest_bytes(original_source.encode()), "transformed_source_sha256": digest_bytes(transformed_source.encode())})
    write_json(case_dir / "transformation.json", transformation)
    token = digest_bytes(canonical_bytes({"candidate": candidate, "source": transformation["original_source_sha256"]}))[:16]
    original = run_execution(task_id=candidate["task_id"], source=original_source, run_dir=case_dir / "original", experiment_name=f"phase0_7_{token}_original")
    if not original.get("execution_success"):
        certificate = {"classification": VALIDITY_UNKNOWN, "reasons": ["BASELINE_REPLAY_FAILED"], "baseline": original}
        write_json(case_dir / "validity_certificate.json", certificate)
        return {"case_id": identifier, "task_id": candidate["task_id"], "baseline": "BASELINE_REPLAY_FAILED", "validity": VALIDITY_UNKNOWN, "relation": "INVALID_FOR_EVALUATOR_TEST", "reason": certificate["reasons"]}
    original_evaluator = evaluator_payload_for_run(candidate["task_id"], original_source, f"phase0_7_{token}_original_evaluator")
    write_json(case_dir / "original" / "evaluator.json", original_evaluator)
    if original_evaluator.get("status") != "COMPLETED" or not original_evaluator["result"].get("success", False):
        certificate = {"classification": VALIDITY_UNKNOWN, "reasons": ["BASELINE_REPLAY_FAILED", "official_evaluator_not_success"], "baseline": original, "original_evaluator": original_evaluator}
        write_json(case_dir / "validity_certificate.json", certificate)
        return {"case_id": identifier, "task_id": candidate["task_id"], "baseline": "BASELINE_REPLAY_FAILED", "validity": VALIDITY_UNKNOWN, "relation": "INVALID_FOR_EVALUATOR_TEST", "reason": certificate["reasons"]}
    transformed = run_execution(task_id=candidate["task_id"], source=transformed_source, run_dir=case_dir / "transformed", experiment_name=f"phase0_7_{token}_transformed")
    target_ids = {candidate["a"]["callee"].removeprefix("apis."), candidate["b"]["callee"].removeprefix("apis.")}
    original_calls = target_call_multimap(original.get("api_calls", []), target_ids)
    transformed_calls = target_call_multimap(transformed.get("api_calls", []), target_ids)
    calls_match = None if original_calls is None or transformed_calls is None else original_calls == transformed_calls
    state_match = None if not original.get("final_state_digest") or not transformed.get("final_state_digest") else original["final_state_digest"] == transformed["final_state_digest"]
    return_match = None if not original.get("return_value_available") or not transformed.get("return_value_available") else original["return_value"] == transformed["return_value"]
    validity, reasons = classify_validity(
        original_initial_digest=original.get("initial_state_digest"), transformed_initial_digest=transformed.get("initial_state_digest"),
        original_execution_ok=bool(original.get("execution_success")), transformed_execution_ok=bool(transformed.get("execution_success")),
        calls_match=calls_match, final_state_match=state_match, returned_value_match=return_match,
    )
    certificate = {"classification": validity, "reasons": reasons, "initial_state_digests": [original.get("initial_state_digest"), transformed.get("initial_state_digest")], "target_call_match": calls_match, "final_state_match": state_match, "returned_value_match": return_match, "original_final_state_digest": original.get("final_state_digest"), "transformed_final_state_digest": transformed.get("final_state_digest")}
    write_json(case_dir / "validity_certificate.json", certificate)
    relation = "INVALID_FOR_EVALUATOR_TEST"
    if validity == VALIDITY_CERTIFIED:
        transformed_evaluator = evaluator_payload_for_run(candidate["task_id"], transformed_source, f"phase0_7_{token}_transformed_evaluator")
        write_json(case_dir / "transformed" / "evaluator.json", transformed_evaluator)
        relation = evaluator_relation(validity, original_evaluator, transformed_evaluator)
    return {"case_id": identifier, "task_id": candidate["task_id"], "baseline": "SUCCESS", "validity": validity, "relation": relation, "reason": reasons}


def evaluator_payload_for_run(task_id: str, source: str, experiment_name: str) -> dict[str, Any]:
    from appworld.environment import AppWorld
    world = AppWorld(task_id=task_id, experiment_name=experiment_name, ground_truth_mode="full")
    try:
        message = world.execute(source + "\nsolution(apis, requester)\n")
        if not execution_ok(message):
            return {"status": "EXECUTION_ERROR", "message": message}
        return evaluator_payload(world)
    finally:
        world.close()


def render_report(queue_info: dict[str, Any], results: list[dict[str, Any]], environment: dict[str, Any]) -> str:
    counts = Counter(result.get("validity", "NOT_ATTEMPTED") for result in results)
    relations = Counter(result.get("relation", "NOT_ATTEMPTED") for result in results)
    certified = counts[VALIDITY_CERTIFIED]
    gate = "MECHANISM_NOT_YET_VALIDATED"
    if certified >= 3:
        gate = "MECHANISM_VALIDATED_CONTROL_ONLY" if not relations["EVALUATOR_VIOLATION_CANDIDATE"] else "MECHANISM_VALIDATED\n\nAdditional signal: DISCOVERY_SIGNAL_PRESENT"
    rows = "\n".join(f"| {item['case_id']} | {item.get('baseline', 'NOT_ATTEMPTED')} | {item['validity']} | {item['relation']} | {', '.join(item.get('reason', []))} |" for item in results)
    return f"""# Phase 0.7: AppWorld Dual Replay Pilot\n\n## Frozen protocol\n\nOnly the Phase 0.6 `POTENTIAL_CANDIDATE` records are used. The sole transformation swaps two complete, direct API-call statements. Each branch reconstructs a new AppWorld from the official task database; evaluator comparison is performed only after a machine validity certificate. No LLM, benchmark modification, candidate re-ranking, evaluator modification, or second transformation is used.\n\n## Environment\n\n```json\n{json.dumps(environment, indent=2, sort_keys=True)}\n```\n\n## Deterministic pilot selection\n\n- Source artifact SHA-256: `{queue_info['source_digest']}`\n- Selection: SHA256(`task_id + call_a_index + call_b_index`), ascending digest\n- Queue SHA-256: `{queue_info['queue_digest']}`\n- Frozen queue size: {len(queue_info['queue'])}\n\n## Initial-state reconstruction and validity certificate\n\nEach execution calls `AppWorld(task_id, ground_truth_mode='full')` independently. Before solution execution, the full loaded model collection is exported and hashed. The full final collection is exported again; all JSON keys and JSONL rows are canonicalized, with no ignored fields. Target API calls are aligned by identity and complete canonical arguments, then their complete returned values are compared. Python return values are captured by the external invocation harness. A case is certified only if both execution branches succeed, initial digests match, target call results match, final snapshots match, and return values match.\n\n## Results\n\n| Case | Baseline | Validity | Evaluator relation | Exclusion / reason |\n| --- | --- | --- | --- | --- |\n{rows}\n\n- Attempted cases: {len(results)}\n- Baseline replay successes: {sum(item.get('baseline') == 'SUCCESS' for item in results)}\n- VALIDITY_CERTIFIED: {counts[VALIDITY_CERTIFIED]}\n- VALIDITY_FAILED: {counts[VALIDITY_FAILED]}\n- VALIDITY_UNKNOWN: {counts[VALIDITY_UNKNOWN]}\n- INVARIANT: {relations['INVARIANT']}\n- EVALUATOR_VIOLATION_CANDIDATE: {relations['EVALUATOR_VIOLATION_CANDIDATE']}\n\n## Methodological limitations\n\nThe strict source transformation supports only direct API-call statements in the same executable statement list. Calls whose log schema cannot expose identity, arguments, and returned value remain `VALIDITY_UNKNOWN`; no field is silently normalized away. This is a pilot limited to the frozen queue and stops after five certified cases or ten attempts.\n\n## Gate decision\n\n`{gate}`\n\n## Exact next recommended step\n\nDo not enter a full benchmark experiment yet. If the gate is mechanism-validated, independently audit any `EVALUATOR_VIOLATION_CANDIDATE`; if all certified cases are invariant, retain AppWorld as a control benchmark and pre-register a separately scoped expansion.\n"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-jsonl", type=Path, default=Path("artifacts/phase0_6/appworld_candidates.jsonl"))
    parser.add_argument("--appworld-root", type=Path, default=Path(environ.get("CERTCF_APPWORLD_ROOT", str(Path(__file__).resolve().parents[1] / "data/appworld"))))
    parser.add_argument("--out-dir", type=Path, default=Path("artifacts/phase0_7"))
    parser.add_argument("--report", type=Path, default=Path("PHASE0_7_APPWORLD_DUAL_REPLAY.md"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = read_jsonl(args.candidate_jsonl)
    queue, queue_digest = select_pilot_queue(records)
    source_digest = digest_bytes(args.candidate_jsonl.read_bytes())
    environment = runtime_environment()
    queue_info = {"version": "PILOT_QUEUE_V1", "selection_algorithm": "SHA256(task_id + call_a_index + call_b_index), ascending", "candidate_source": str(args.candidate_jsonl), "source_digest": source_digest, "queue_digest": queue_digest, "queue": queue}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.out_dir / "PILOT_QUEUE_V1.json", queue_info)
    results: list[dict[str, Any]] = []
    certified = 0
    for candidate in queue:
        if certified >= 5:
            break
        result = run_case(candidate, data_root=args.appworld_root, cases_dir=args.out_dir / "cases", environment=environment)
        results.append(result)
        certified += result["validity"] == VALIDITY_CERTIFIED
    write_json(args.out_dir / "results.json", {"queue_digest": queue_digest, "results": results})
    args.report.write_text(render_report(queue_info, results, environment))


if __name__ == "__main__":
    main()
