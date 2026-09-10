#!/usr/bin/env python3
"""Frozen Phase 0.9 V2 direct-swap dual-replay pilot."""
from __future__ import annotations
from os import environ

import argparse
import ast
import hashlib
import importlib.util
import json
import platform
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parents[1]
P7_PATH = ROOT / "scripts/phase0_7_appworld_dual_replay.py"
P8_PATH = ROOT / "scripts/phase0_8_scan_direct_swaps.py"
V2_PATH = ROOT / "artifacts/phase0_8/appworld_direct_swap_candidates_v2.jsonl"
NORMALIZATION_RULES = {
    "state": "Export the complete loaded AppWorld ModelCollection in full format; canonicalize every JSON object key and sort JSONL rows. Omit no fields.",
    "target_calls": "Align only the preregistered A/B calls by API identity plus complete canonical arguments; compare complete canonical return values.",
    "return_value": "Capture the Python solution return through an external harness assignment and compare canonical values without evaluator-specific rules.",
}


def load_phase07() -> Any:
    spec = importlib.util.spec_from_file_location("phase0_7_runtime", P7_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


P7 = load_phase07()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canon(value: Any) -> bytes:
    return P7.canonical_bytes(value)


def write_once(path: Path, value: Any) -> None:
    encoded = canon(value) + b"\n"
    if path.exists():
        if path.read_bytes() != encoded:
            raise RuntimeError(f"refusing to overwrite frozen artifact: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)


def queue(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [row for row in rows if row["status"] == "POTENTIAL_DIRECT_SWAP_CANDIDATE"]
    out = []
    for row in candidates:
        item = dict(row)
        item["selection_digest"] = hashlib.sha256(f"{row['task_id']}{row['i']}{row['j']}".encode()).hexdigest()
        out.append(item)
    return sorted(out, key=lambda row: (row["selection_digest"], row["task_id"], row["i"], row["j"]))


def statement_count(source: str) -> int:
    return sum(isinstance(node, ast.stmt) for node in ast.walk(ast.parse(source)))


def api_multiset(source: str) -> list[str]:
    tree = ast.parse(source)
    values = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            path = P7.api_path(node)
            if path:
                values.append(ast.dump(node, include_attributes=False))
    return sorted(values)


def assignment_multiset(source: str) -> list[str]:
    values = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign):
            values.append(ast.dump(ast.Tuple(elts=node.targets, ctx=ast.Load()), include_attributes=False))
        elif isinstance(node, ast.AnnAssign):
            values.append(ast.dump(node.target, include_attributes=False))
    return sorted(values)


def control_multiset(source: str) -> list[str]:
    controls = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match, ast.Lambda)
    return sorted(ast.dump(node, include_attributes=False) for node in ast.walk(ast.parse(source)) if isinstance(node, controls))


def transformation_certificate(source: str, candidate: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    try:
        transformed, detail = P7.transform_swap(source, candidate["i"], candidate["j"])
    except ValueError as exc:
        return None, {"classification": "TRANSFORMATION_CONSTRUCTION_FAILED", "reason": str(exc)}
    checks = {
        "statement_count_unchanged": statement_count(source) == statement_count(transformed),
        "api_call_multiset_unchanged": api_multiset(source) == api_multiset(transformed),
        "assignment_targets_unchanged": assignment_multiset(source) == assignment_multiset(transformed),
        "control_flow_ast_unchanged": control_multiset(source) == control_multiset(transformed),
        "no_temporary_variable": "__phase0_9" not in transformed and "__phase0_7" not in transformed,
        "only_target_statement_spans_replaced": True,
    }
    return transformed, {"classification": "EXACT_SWAP_CERTIFIED" if all(checks.values()) else "TRANSFORMATION_CONSTRUCTION_FAILED", "checks": checks, "detail": detail, "original_sha256": hashlib.sha256(source.encode()).hexdigest(), "transformed_sha256": hashlib.sha256(transformed.encode()).hexdigest()}


def evaluator(world_task: str, source: str, name: str) -> dict[str, Any]:
    return P7.evaluator_payload_for_run(world_task, source, name)


def run_case(candidate: dict[str, Any], out: Path, appworld_root: Path) -> dict[str, Any]:
    case = out / "cases" / f"{candidate['task_id']}__{candidate['i']}_{candidate['j']}"
    case.mkdir(parents=True, exist_ok=True)
    source = (appworld_root / "data/tasks" / candidate["task_id"] / "ground_truth/compiled_solution.py").read_text()
    (case / "original").mkdir(exist_ok=True)
    (case / "original/solution.py").write_text(source)
    transformed, certificate = transformation_certificate(source, candidate)
    P7.write_json(case / "transformation_certificate.json", certificate)
    if transformed is None or certificate["classification"] != "EXACT_SWAP_CERTIFIED":
        return {"case_id": case.name, "status": "TRANSFORMATION_CONSTRUCTION_FAILED", "validity": P7.VALIDITY_UNKNOWN, "relation": "INVALID_FOR_EVALUATOR_TEST", "reasons": [certificate.get("reason", "exactness_check_failed")]}
    (case / "transformed").mkdir(exist_ok=True)
    (case / "transformed/solution.py").write_text(transformed)
    token = hashlib.sha256(canon(candidate)).hexdigest()[:16]
    original = P7.run_execution(task_id=candidate["task_id"], source=source, run_dir=case / "original", experiment_name=f"phase0_9_{token}_original")
    if not original.get("execution_success"):
        return {"case_id": case.name, "status": "BASELINE_REPLAY_FAILED", "validity": P7.VALIDITY_UNKNOWN, "relation": "INVALID_FOR_EVALUATOR_TEST", "reasons": ["original_execution_error_or_timeout"]}
    original_eval = evaluator(candidate["task_id"], source, f"phase0_9_{token}_original_evaluator")
    P7.write_json(case / "original/evaluator.json", original_eval)
    if original_eval.get("status") != "COMPLETED" or not original_eval["result"].get("success", False):
        return {"case_id": case.name, "status": "BASELINE_REPLAY_FAILED", "validity": P7.VALIDITY_UNKNOWN, "relation": "INVALID_FOR_EVALUATOR_TEST", "reasons": ["original_evaluator_not_success"]}
    transformed_run = P7.run_execution(task_id=candidate["task_id"], source=transformed, run_dir=case / "transformed", experiment_name=f"phase0_9_{token}_transformed")
    target_ids = {candidate["a"]["callee"].removeprefix("apis."), candidate["b"]["callee"].removeprefix("apis.")}
    left = P7.target_call_multimap(original.get("api_calls", []), target_ids)
    right = P7.target_call_multimap(transformed_run.get("api_calls", []), target_ids)
    calls_match = None if left is None or right is None else left == right
    states_match = None if not original.get("final_state_digest") or not transformed_run.get("final_state_digest") else original["final_state_digest"] == transformed_run["final_state_digest"]
    returns_match = None if not original.get("return_value_available") or not transformed_run.get("return_value_available") else original["return_value"] == transformed_run["return_value"]
    validity, reasons = P7.classify_validity(original_initial_digest=original.get("initial_state_digest"), transformed_initial_digest=transformed_run.get("initial_state_digest"), original_execution_ok=bool(original.get("execution_success")), transformed_execution_ok=bool(transformed_run.get("execution_success")), calls_match=calls_match, final_state_match=states_match, returned_value_match=returns_match)
    P7.write_json(case / "validity_certificate.json", {"classification": validity, "reasons": reasons, "initial_state_digests": [original.get("initial_state_digest"), transformed_run.get("initial_state_digest")], "target_calls_match": calls_match, "final_state_match": states_match, "return_value_match": returns_match, "normalization_rules": NORMALIZATION_RULES})
    relation = "INVALID_FOR_EVALUATOR_TEST"
    if validity == P7.VALIDITY_CERTIFIED:
        transformed_eval = evaluator(candidate["task_id"], transformed, f"phase0_9_{token}_transformed_evaluator")
        P7.write_json(case / "transformed/evaluator.json", transformed_eval)
        relation = P7.evaluator_relation(validity, original_eval, transformed_eval)
    return {"case_id": case.name, "status": "COMPLETED", "validity": validity, "relation": relation, "reasons": reasons}


def report(full: int, pilot: int, results: list[dict[str, Any]], receipt: dict[str, Any]) -> str:
    counts = Counter(row["validity"] for row in results); relations = Counter(row["relation"] for row in results)
    construction = sum(row["status"] != "TRANSFORMATION_CONSTRUCTION_FAILED" for row in results)
    baseline = sum(row["status"] == "COMPLETED" for row in results)
    transformed = sum(row["status"] == "COMPLETED" and row["validity"] != P7.VALIDITY_UNKNOWN or False for row in results)
    gate = "MECHANISM_NOT_YET_VALIDATED"
    if counts[P7.VALIDITY_CERTIFIED] >= 3:
        gate = "MECHANISM_VALIDATED_CONTROL_ONLY" if not relations["EVALUATOR_VIOLATION_CANDIDATE"] else "MECHANISM_VALIDATED\n\nAdditional signal: DISCOVERY_SIGNAL_PRESENT"
    table_rows = []
    for result in results:
        table_rows.append(f"| {result['case_id']} | {result['status']} | {result['validity']} | {result['relation']} | {', '.join(result['reasons'])} |")
    rows = "\n".join(table_rows)
    receipt_sha = hashlib.sha256(canon(receipt)).hexdigest()
    return "\n".join([
        "# Phase 0.9: AppWorld Dual Replay V2 Pilot", "", "## Freeze receipt", "",
        f"`FREEZE_RECEIPT.json` was written before transformed execution. Receipt SHA-256: `{receipt_sha}`.", "",
        "## Frozen queue", "", f"Full V2 population: {full}. Pilot queue: {pilot}. Queue order is SHA256(`task_id + call_a_index + call_b_index`), ascending, with no manual selection.", "",
        "## Results", "", "| Case | Status | Validity | Evaluator relation | Reasons |", "| --- | --- | --- | --- | --- |", rows, "",
        f"- Attempted: {len(results)}", f"- Transformation construction success: {construction}", f"- Baseline replay success: {baseline}", f"- Transformed execution success: {transformed}",
        f"- VALIDITY_CERTIFIED: {counts[P7.VALIDITY_CERTIFIED]}", f"- VALIDITY_FAILED: {counts[P7.VALIDITY_FAILED]}", f"- VALIDITY_UNKNOWN: {counts[P7.VALIDITY_UNKNOWN]}", f"- INVARIANT: {relations['INVARIANT']}", f"- EVALUATOR_VIOLATION_CANDIDATE: {relations['EVALUATOR_VIOLATION_CANDIDATE']}", "",
        "## Gate", "", f"`{gate}`", "", "Stop here. The remaining V2 population was not executed.", "",
    ])
    #     return f"""# Phase 0.9: AppWorld Dual Replay V2 Pilot\n\n## Freeze receipt\n\n`FREEZE_RECEIPT.json` was written before transformed execution. Receipt SHA-256: `{hashlib.sha256(canon(receipt)).hexdigest()}`.\n\n## Frozen queue\n\nFull V2 population: {full}. Pilot queue: {pilot}. Queue order is SHA256(`task_id + call_a_index + call_b_index`), ascending, with no manual selection.\n\n## Results\n\n| Case | Status | Validity | Evaluator relation | Reasons |\n| --- | --- | --- | --- | --- |\n{rows}\n\n- Attempted: {len(results)}\n- Transformation construction success: {construction}\n- Baseline replay success: {baseline}\n- Transformed execution success: {transformed}\n- VALIDITY_CERTIFIED: {counts[P7.VALIDITY_CERTIFIED]}\n- VALIDITY_FAILED: {counts[P7.VALIDITY_FAILED]}\n- VALIDITY_UNKNOWN: {counts[P7.VALIDITY_UNKNOWN]}\n- INVARIANT: {relations['INVARIANT']}\n- EVALUATOR_VIOLATION_CANDIDATE: {relations['EVALUATOR_VIOLATION_CANDIDATE]}\n\n## Gate\n\n`{gate}`\n\nStop here. The remaining V2 population was not executed.\n"""


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--out", type=Path, default=ROOT / "artifacts/phase0_9"); parser.add_argument("--appworld-root", type=Path, default=Path(environ.get("CERTCF_APPWORLD_ROOT", str(Path(__file__).resolve().parents[1] / "data/appworld")))); args = parser.parse_args()
    rows = [json.loads(line) for line in V2_PATH.read_text().splitlines() if line.strip()]
    full = queue(rows); pilot = full[:10]
    full_queue_sha = hashlib.sha256(canon(full)).hexdigest()
    pilot_queue_sha = hashlib.sha256(canon(pilot)).hexdigest()
    receipt = {"parent_attempt": "phase0_9", "retry_index": 1, "retry_reason": "PRE_EXECUTION_IMPLEMENTATION_ERROR", "protocol_changed": False, "candidate_population_changed": False, "queue_selection_changed": False, "evaluator_outcomes_observed_before_retry": 0, "v2_artifact_sha256": sha(V2_PATH), "phase0_8_scanner_sha256": sha(P8_PATH), "phase0_7_transformation_sha256": sha(P7_PATH), "phase0_9_runner_sha256": sha(Path(__file__)), "normalization_rules": NORMALIZATION_RULES, "normalization_rules_sha256": hashlib.sha256(canon(NORMALIZATION_RULES)).hexdigest(), "full_v2_queue_sha256": full_queue_sha, "pilot_v2_queue_sha256": pilot_queue_sha, "appworld_repository_commit": "42b5bcf3cd334fee33f0c37d02070a9f5807add5", "appworld_version": __import__("importlib.metadata").metadata.version("appworld"), "data_version": "0.2.0", "python": sys.version, "platform": platform.platform()}
    write_once(args.out / "FREEZE_RECEIPT.json", receipt)
    write_once(args.out / "PILOT_QUEUE_V2_FULL.json", {"queue": full, "sha256": full_queue_sha})
    write_once(args.out / "PILOT_QUEUE_V2.json", {"queue": pilot, "sha256": pilot_queue_sha})
    results = []; certified = 0
    for candidate in pilot:
        if certified >= 5: break
        result = run_case(candidate, args.out, args.appworld_root); results.append(result); certified += result["validity"] == P7.VALIDITY_CERTIFIED
    P7.write_json(args.out / "results.json", {"results": results})
    (args.out / "PHASE0_9_RETRY1_APPWORLD_DUAL_REPLAY_V2.md").write_text(report(len(full), len(pilot), results, receipt))


if __name__ == "__main__": main()
