#!/usr/bin/env python3
"""Prospective AppWorld R2A validation on task-level unexposed train/dev cases."""

from __future__ import annotations
from os import environ

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA = Path(environ.get("CERTCF_APPWORLD_ROOT", str(Path(__file__).resolve().parents[1] / "data/appworld")))
OUT = ROOT / "artifacts" / "validation_e3"
PROTOCOL = ROOT / "E3_APPWORLD_PROSPECTIVE_VALIDATION_PROTOCOL.md"
EXPECTED_COMMIT = "42b5bcf3cd334fee33f0c37d02070a9f5807add5"
RULE_VERSION = "appworld_r2a_direct_field_v1"
FIELD_NAMES = {"amount", "body", "content", "description", "message", "name", "subject", "text", "title"}
FREE_TEXT_FIELDS = {"body", "content", "description", "message", "name", "subject", "text", "title"}
EXCLUDED_ARGUMENTS = {"access_token", "password", "token", "query", "page_index"}
CONTROL_KWARGS = {"_app_name", "_api_name", "client", "raise_on_failure", "show", "track"}
os.environ["APPWORLD_ROOT"] = str(DATA)


def import_file(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


OBS = import_file("e3_obs", ROOT / "scripts" / "phase0_9_observability_calibration.py")
P7 = OBS.P7
P45 = import_file("e3_p45", ROOT / "scripts" / "phase4_5_scan_appworld_r3v2.py")


def canonical(value: Any) -> Any:
    return P7.canonical(value)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    P7.write_json(path, value)


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float)) and not isinstance(value, bool)


def occurrences(text: str, value: Any) -> list[re.Match[str]]:
    raw = str(value)
    if len(raw) < 3:
        return []
    return list(re.finditer(r"(?<![\w])" + re.escape(raw) + r"(?![\w])", text, re.I))


def mutation(value: Any, field: str) -> tuple[Any | None, str | None]:
    if isinstance(value, int) and value >= 0:
        return value + 1, "integer_plus_one_v1"
    if isinstance(value, float) and value > 0:
        return round(value + 0.01, 10), "positive_float_plus_0_01_v1"
    if isinstance(value, str) and field in FREE_TEXT_FIELDS:
        try:
            parsed = date.fromisoformat(value)
            return (parsed + timedelta(days=1)).isoformat(), "iso_date_plus_one_day_v1"
        except ValueError:
            return value + " [alternate]", "free_text_suffix_v1"
    return None, None


def task_ids() -> list[tuple[str, str]]:
    rows = []
    for split in ("train", "dev"):
        rows.extend((split, item.strip()) for item in (DATA / "data" / "datasets" / f"{split}.txt").read_text().splitlines() if item.strip())
    if len(rows) != 147:
        raise RuntimeError(f"expected 147 train/dev tasks, got {len(rows)}")
    return rows


def exposed_tasks(known: set[str]) -> tuple[set[str], list[dict[str, str]]]:
    exposed: set[str] = set()
    proof = []
    for branch in ("transformed", "perturbed", "omission"):
        for path in sorted((ROOT / "artifacts").glob(f"**/{branch}/execution.json")):
            case_dir = path.parent.parent
            task = None
            metadata = case_dir / "metadata.json"
            if metadata.exists():
                task = json.loads(metadata.read_text()).get("task_id")
            if task not in known:
                prefix = case_dir.name.split("__", 1)[0]
                task = prefix if prefix in known else None
            if task in known:
                exposed.add(task)
                proof.append({"task_id": task, "evidence": str(path.relative_to(ROOT)), "branch": branch})
    return exposed, proof


def original_records() -> dict[str, dict[str, Any]]:
    records = {}
    for _, task_id in task_ids():
        path = ROOT / "artifacts" / "phase4_5" / "originals" / task_id / "execution.json"
        if not path.exists():
            raise RuntimeError(f"missing original-only evidence: {task_id}")
        records[task_id] = json.loads(path.read_text())
    return records


def producer_matches(run: dict[str, Any], call: dict[str, Any], field: str, value: Any) -> list[dict[str, Any]]:
    matches = []
    for change in run["persistent_changes"]:
        record = change.get("record") or {}
        if field not in record or canonical(record[field]) != canonical(value):
            continue
        if not change["model"].startswith(call["api_identity"].split(".", 1)[0] + "."):
            continue
        if field not in change.get("changed_fields", []) and change["operation"] == "update":
            continue
        matches.append(change)
    return matches


def scan() -> None:
    if (OUT / "STATIC_CANDIDATES_V1.jsonl").exists():
        raise RuntimeError("E3 static population exists; refusing overwrite")
    ids = task_ids()
    known = {task for _, task in ids}
    exposed, proof = exposed_tasks(known)
    records = original_records()
    rejections = Counter()
    candidates = []
    for split, task_id in ids:
        run = records[task_id]
        if task_id in exposed:
            rejections["PRIOR_COUNTERFACTUAL_EXPOSURE"] += 1
            continue
        baseline = run.get("execution_success") and run.get("evaluator", {}).get("status") == "COMPLETED" and run["evaluator"]["result"].get("success") is True
        if not baseline:
            rejections["ORIGINAL_BASELINE_NOT_PASS"] += 1
            continue
        instruction = run["instruction"]
        task_candidates = []
        for call in run["api_trace"]:
            arguments = call.get("arguments", {})
            for argument, value in arguments.items():
                if argument in EXCLUDED_ARGUMENTS or argument not in FIELD_NAMES or not scalar(value):
                    continue
                hits = occurrences(instruction, value)
                if len(hits) != 1:
                    continue
                changes = producer_matches(run, call, argument, value)
                if len(changes) != 1:
                    continue
                changed, rule = mutation(value, argument)
                if changed is None or type(changed) is not type(value) or canonical(changed) == canonical(value):
                    continue
                same_role_producers = []
                for other in run["api_trace"]:
                    if canonical(other.get("arguments", {}).get(argument)) != canonical(value):
                        continue
                    if producer_matches(run, other, argument, value):
                        same_role_producers.append(other["sequence"])
                if same_role_producers != [call["sequence"]]:
                    continue
                change = changes[0]
                solution = DATA / "data" / "tasks" / task_id / "ground_truth" / "compiled_solution.py"
                row = {
                    "status": "POTENTIAL_E3_APPWORLD_R2A_CANDIDATE",
                    "task_id": task_id,
                    "split": split,
                    "instruction": instruction,
                    "instruction_span": {"text": hits[0].group(), "start": hits[0].start(), "end": hits[0].end()},
                    "target_call_index": call["sequence"],
                    "target_api_identity": call["api_identity"],
                    "target_argument": argument,
                    "original_value": value,
                    "mutation_value": changed,
                    "mutation_rule": rule,
                    "mutation_rule_version": RULE_VERSION,
                    "effect_model": change["model"],
                    "effect_operation": change["operation"],
                    "effect_record_id": change["record_id"],
                    "effect_field": argument,
                    "original_effect_witness": change,
                    "producer_indices": same_role_producers,
                    "compiled_solution_sha256": file_sha(solution),
                    "original_final_state_digest": run["final_digest"],
                    "prior_counterfactual_exposure": False,
                }
                row["candidate_id"] = digest({key: row[key] for key in ("task_id", "target_call_index", "target_api_identity", "target_argument", "original_value", "mutation_value", "effect_model", "effect_field", "mutation_rule_version")})[:20]
                row["representative_digest"] = digest([task_id, call["sequence"], argument, value, change["model"], argument, RULE_VERSION])
                task_candidates.append(row)
        if not task_candidates:
            rejections["NO_DIRECT_TASK_EFFECT_CANDIDATE"] += 1
        candidates.extend(task_candidates)
    candidates.sort(key=lambda row: row["candidate_id"])
    representatives = []
    by_task = defaultdict(list)
    for row in candidates:
        by_task[row["task_id"]].append(row)
    for task, rows in by_task.items():
        rep = min(rows, key=lambda row: (row["representative_digest"], row["candidate_id"]))
        item = copy.deepcopy(rep)
        item["task_rank_digest"] = digest([task, item["compiled_solution_sha256"]])
        representatives.append(item)
    representatives.sort(key=lambda row: (row["task_rank_digest"], row["task_id"]))
    if len(representatives) >= 10:
        queue, gate = representatives[:10], "E3_READY_FOR_PROSPECTIVE_PILOT"
    elif len(representatives) >= 5:
        queue, gate = representatives, "E3_CONDITIONAL_SMALL_PROSPECTIVE_VALIDATION"
    else:
        queue, gate = [], "E3_NO_GO_INSUFFICIENT_UNEXPOSED_OPPORTUNITY"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "STATIC_CANDIDATES_V1.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in candidates))
    write(OUT / "TASK_REPRESENTATIVES_V1.json", representatives)
    write(OUT / "PILOT_QUEUE_V1.json", queue)
    write(OUT / "EXPOSURE_REGISTRY_V1.json", {"excluded_task_ids": sorted(exposed), "proof": proof})
    result = {
        "phase": "E3_STATIC",
        "appworld_tasks": len(ids),
        "prior_counterfactual_exposed_tasks": len(exposed),
        "unexposed_tasks": len(ids) - len(exposed),
        "candidate_arguments": len(candidates),
        "candidate_tasks": len(representatives),
        "pilot_tasks": len(queue),
        "gate": gate,
        "rejections": dict(sorted(rejections.items())),
        "candidate_sha256": file_sha(OUT / "STATIC_CANDIDATES_V1.jsonl"),
        "queue_sha256": file_sha(OUT / "PILOT_QUEUE_V1.json"),
        "counterfactual_executions": 0,
        "counterfactual_evaluator_outcomes": 0,
        "llm_api_calls": 0,
    }
    write(OUT / "static_results.json", result)
    print(json.dumps(result, indent=2, sort_keys=True))


def freeze() -> None:
    if (OUT / "FREEZE_RECEIPT.json").exists():
        raise RuntimeError("E3 receipt exists; refusing overwrite")
    static = json.loads((OUT / "static_results.json").read_text())
    if not static["gate"].startswith("E3_READY") and not static["gate"].startswith("E3_CONDITIONAL"):
        raise RuntimeError(static["gate"])
    receipt = {
        "phase": "E3",
        "status": "FROZEN_PRE_COUNTERFACTUAL_EXECUTION",
        "retrospective_ablation": False,
        "benchmark_previously_used_for_other_relations": True,
        "selected_tasks_prior_counterfactual_exposure": False,
        "appworld_commit": EXPECTED_COMMIT,
        "appworld_package": "0.2.0.dev0",
        "appworld_data": "0.2.0",
        "protocol_sha256": file_sha(PROTOCOL),
        "candidate_population_sha256": file_sha(OUT / "STATIC_CANDIDATES_V1.jsonl"),
        "exposure_registry_sha256": file_sha(OUT / "EXPOSURE_REGISTRY_V1.json"),
        "representatives_sha256": file_sha(OUT / "TASK_REPRESENTATIVES_V1.json"),
        "pilot_queue_sha256": file_sha(OUT / "PILOT_QUEUE_V1.json"),
        "runner_sha256": file_sha(Path(__file__)),
        "r2a_prior_freeze_sha256": file_sha(ROOT / "artifacts" / "phase2_4" / "FREEZE_RECEIPT.json"),
        "counterfactual_executions_before_freeze": 0,
        "counterfactual_evaluator_outcomes_before_freeze": 0,
        "candidate_selection_manual": False,
        "queue_selection_manual": False,
        "evaluator_driven_selection": False,
        "issue_pr_search_performed": False,
        "llm_api_calls": 0,
        "python": sys.version,
        "platform": platform.platform(),
    }
    write(OUT / "FREEZE_RECEIPT.json", receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))


def snapshot_changes(initial: Path, final: Path) -> list[dict[str, Any]]:
    holder = OUT / "_snapshot_adapter"
    holder.mkdir(parents=True, exist_ok=True)
    # Reuse the frozen Phase 4.5 database diff implementation through its expected layout.
    linked_initial, linked_final = holder / "initial", holder / "final"
    if linked_initial.exists() or linked_initial.is_symlink():
        linked_initial.unlink()
    if linked_final.exists() or linked_final.is_symlink():
        linked_final.unlink()
    linked_initial.symlink_to(initial)
    linked_final.symlink_to(final)
    try:
        return P45.snapshot_changes(holder)
    finally:
        linked_initial.unlink()
        linked_final.unlink()


def run_branch(candidate: dict[str, Any], branch: str, mutate: bool) -> dict[str, Any]:
    from appworld.environment import AppWorld

    task_id = candidate["task_id"]
    case = OUT / "cases" / candidate["candidate_id"] / branch
    case.mkdir(parents=True, exist_ok=True)
    source = (DATA / "data" / "tasks" / task_id / "ground_truth" / "compiled_solution.py").read_text()
    world = AppWorld(task_id=task_id, experiment_name=f"e3_{candidate['candidate_id']}_{branch}", ground_truth_mode="full")
    trace = []
    mutation_applied = False
    try:
        world.models.save(str(case / "initial"), format="full", delete_if_exists=True)
        initial_digest, _ = P7.file_tree_digest(case / "initial")
        raw = world.requester.request

        def traced(*args, **kwargs):
            nonlocal mutation_applied
            app = kwargs.get("_app_name", args[0] if args else None)
            api = kwargs.get("_api_name", args[1] if len(args) > 1 else None)
            identity = f"{app}.{api}"
            index = len(trace)
            before = {key: canonical(value) for key, value in kwargs.items() if key not in CONTROL_KWARGS}
            if mutate and index == candidate["target_call_index"]:
                if identity != candidate["target_api_identity"]:
                    raise RuntimeError("target API identity mismatch")
                if canonical(before.get(candidate["target_argument"])) != canonical(candidate["original_value"]):
                    raise RuntimeError("target original argument mismatch")
                kwargs[candidate["target_argument"]] = candidate["mutation_value"]
                mutation_applied = True
            actual = {key: canonical(value) for key, value in kwargs.items() if key not in CONTROL_KWARGS}
            entry = {"sequence": index, "api_identity": identity, "arguments": actual, "planned_original_arguments": before}
            try:
                result = raw(*args, **kwargs)
                entry["response"] = canonical(result)
                trace.append(entry)
                return result
            except Exception as exc:
                entry["exception"] = {"type": type(exc).__name__, "message": str(exc)}
                trace.append(entry)
                raise

        world.requester.request = traced
        message = world.execute(source + "\n__e3_return = solution(apis, requester)\n")
        execution_success = P7.execution_ok(message)
        evaluator = P7.evaluator_payload(world) if execution_success else {"status": "NOT_RUN"}
        world.models.save(str(case / "final"), format="full", delete_if_exists=True)
        final_digest, _ = P7.file_tree_digest(case / "final")
        changes = snapshot_changes(case / "initial", case / "final") if execution_success else []
        payload = {
            "execution_success": execution_success,
            "message": message,
            "mutation_applied": mutation_applied,
            "initial_digest": initial_digest,
            "final_digest": final_digest,
            "trace": trace,
            "persistent_changes": changes,
            "evaluator": evaluator,
        }
        write(case / "execution.json", payload)
        write(case / "trajectory.json", trace)
        write(case / "persistent_changes.json", changes)
        write(case / "evaluator.json", evaluator)
        return payload
    finally:
        world.close()


def exactness(original: dict[str, Any], changed: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    left, right = original["trace"], changed["trace"]
    differences = []
    if len(left) == len(right):
        for index, (a, b) in enumerate(zip(left, right)):
            keys = set(a.get("arguments", {})) | set(b.get("arguments", {}))
            for key in keys:
                if canonical(a.get("arguments", {}).get(key)) != canonical(b.get("arguments", {}).get(key)):
                    differences.append([index, key, a.get("arguments", {}).get(key), b.get("arguments", {}).get(key)])
    checks = {
        "trace_length_unchanged": len(left) == len(right),
        "tool_identity_sequence_unchanged": [x["api_identity"] for x in left] == [x["api_identity"] for x in right],
        "exactly_one_argument_changed": len(differences) == 1,
        "target_change_exact": differences == [[candidate["target_call_index"], candidate["target_argument"], candidate["original_value"], candidate["mutation_value"]]],
        "mutation_applied": changed["mutation_applied"] is True,
    }
    return {"classification": "MUTATION_EXACTNESS_CERTIFIED" if all(checks.values()) else "MUTATION_EXACTNESS_FAILED", "checks": checks, "differences": differences}


def effect_certificate(candidate: dict[str, Any], original: dict[str, Any], changed: dict[str, Any], exact: dict[str, Any]) -> dict[str, Any]:
    model, field = candidate["effect_model"], candidate["effect_field"]
    original_effects = [x for x in original["persistent_changes"] if x["model"] == model and canonical((x.get("record") or {}).get(field)) == canonical(candidate["original_value"])]
    wrong_effects = [x for x in changed["persistent_changes"] if x["model"] == model and canonical((x.get("record") or {}).get(field)) == canonical(candidate["mutation_value"])]
    correct_effects = [x for x in changed["persistent_changes"] if x["model"] == model and canonical((x.get("record") or {}).get(field)) == canonical(candidate["original_value"])]
    initial_equal = original["initial_digest"] == changed["initial_digest"]
    target_call_success = (
        changed["execution_success"]
        and len(changed["trace"]) > candidate["target_call_index"]
        and "exception" not in changed["trace"][candidate["target_call_index"]]
    )
    checks = {
        "explicit_unique_literal": True,
        "original_effect_witness": len(original_effects) >= 1,
        "independent_initial_state_equal": initial_equal,
        "exact_mutation": exact["classification"] == "MUTATION_EXACTNESS_CERTIFIED",
        "target_action_success": target_call_success,
        "wrong_persistent_effect": len(wrong_effects) >= 1,
        "correct_effect_absent": len(correct_effects) == 0,
        "no_compensation": len(correct_effects) == 0,
        "same_semantic_role": candidate["target_argument"] == candidate["effect_field"],
    }
    classification = "TASK_CRITICAL_VIOLATION_CERTIFIED" if all(checks.values()) else (
        "MUTATION_EXECUTION_INVALID" if not target_call_success else "TASK_CRITICAL_VIOLATION_UNKNOWN"
    )
    return {"classification": classification, "checks": checks, "original_witnesses": original_effects, "wrong_effect_witnesses": wrong_effects, "correct_effect_witnesses": correct_effects}


def execute() -> None:
    if not (OUT / "FREEZE_RECEIPT.json").exists():
        raise RuntimeError("freeze receipt required")
    queue = json.loads((OUT / "PILOT_QUEUE_V1.json").read_text())
    results = []
    for number, candidate in enumerate(queue, 1):
        case = OUT / "cases" / candidate["candidate_id"]
        case.mkdir(parents=True, exist_ok=True)
        write(case / "metadata.json", candidate)
        original = run_branch(candidate, "original", False)
        original_pass = original["execution_success"] and original["evaluator"].get("status") == "COMPLETED" and original["evaluator"]["result"].get("success") is True
        if not original_pass:
            result = {"candidate_id": candidate["candidate_id"], "task_id": candidate["task_id"], "classification": "BASELINE_REPLAY_FAILED", "gates": {"G0_LITERAL_ONLY": True, "G1_EXECUTION_SUCCESS": False, "G2_SIMPLE_EFFECT": False, "G3_FULL_TASK_EFFECT_CERTIFICATE": False}}
            write(case / "result.json", result)
            results.append(result)
            continue
        changed = run_branch(candidate, "counterfactual", True)
        exact = exactness(original, changed, candidate)
        certificate = effect_certificate(candidate, original, changed, exact)
        write(case / "mutation_exactness_certificate.json", exact)
        write(case / "task_critical_effect_certificate.json", certificate)
        g1 = changed["execution_success"] and original["initial_digest"] == changed["initial_digest"]
        target_response_differs = False
        if g1 and len(original["trace"]) > candidate["target_call_index"] and len(changed["trace"]) > candidate["target_call_index"]:
            target_response_differs = canonical(original["trace"][candidate["target_call_index"]].get("response")) != canonical(changed["trace"][candidate["target_call_index"]].get("response"))
        g2 = g1 and (original["final_digest"] != changed["final_digest"] or target_response_differs)
        g3 = g2 and certificate["classification"] == "TASK_CRITICAL_VIOLATION_CERTIFIED"
        changed_pass = changed["evaluator"].get("status") == "COMPLETED" and changed["evaluator"]["result"].get("success") is True
        result = {
            "candidate_id": candidate["candidate_id"],
            "task_id": candidate["task_id"],
            "classification": certificate["classification"],
            "gates": {"G0_LITERAL_ONLY": True, "G1_EXECUTION_SUCCESS": g1, "G2_SIMPLE_EFFECT": g2, "G3_FULL_TASK_EFFECT_CERTIFICATE": g3},
            "original_evaluator_pass": True,
            "counterfactual_evaluator_pass": changed_pass,
            "full_gate_evaluator_relation": ("FALSE_ACCEPTANCE_CANDIDATE" if changed_pass else "SENSITIVE") if g3 else None,
        }
        write(case / "result.json", result)
        results.append(result)
        print(f"{number}/{len(queue)} {candidate['task_id']} {certificate['classification']} {result['full_gate_evaluator_relation'] or ''}", flush=True)
    write(OUT / "results.json", aggregate(results, queue))
    print(json.dumps(json.loads((OUT / "results.json").read_text()), indent=2, sort_keys=True))


def aggregate(results: list[dict[str, Any]], queue: list[dict[str, Any]]) -> dict[str, Any]:
    gates = {}
    for gate in ("G0_LITERAL_ONLY", "G1_EXECUTION_SUCCESS", "G2_SIMPLE_EFFECT", "G3_FULL_TASK_EFFECT_CERTIFICATE"):
        admitted = [row for row in results if row["gates"][gate]]
        claims = [row for row in admitted if row.get("counterfactual_evaluator_pass")]
        gates[gate] = {"retained_pairs": len(admitted), "retained_tasks": len({row["task_id"] for row in admitted}), "official_pass_claims": len(claims)}
    classes = Counter(row["classification"] for row in results)
    relations = Counter(row.get("full_gate_evaluator_relation") for row in results if row.get("full_gate_evaluator_relation"))
    return {
        "phase": "E3",
        "prospective_task_validation": True,
        "population": len(queue),
        "attempted": len(results),
        "gate_results": gates,
        "certificate_classes": dict(sorted(classes.items())),
        "full_gate_evaluator_relations": dict(sorted(relations.items())),
        "independent_semantic_adjudication": "PENDING",
        "new_counterfactual_executions": sum(row["classification"] != "BASELINE_REPLAY_FAILED" for row in results),
        "llm_api_calls": 0,
        "issue_pr_search_performed": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("scan", "freeze", "execute"))
    args = parser.parse_args()
    {"scan": scan, "freeze": freeze, "execute": execute}[args.mode]()
