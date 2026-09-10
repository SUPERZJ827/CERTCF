#!/usr/bin/env python3
"""Phase 0.6 static candidate scanner for AppWorld.

This script performs a conservative, read-only static scan and emits:
- summary statistics
- per-task rejection reasons
- per-pair candidate/rejection records as jsonl

Output semantics are intentionally minimal and only for offline feasibility screening.
"""

from __future__ import annotations
from os import environ

import argparse
import ast
import json
import os
import random
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence


OrderRequirementResult = dict[str, Any]


REJECT_REASON_TASK_ORDER = "TASK_SPEC_EXPLICIT_ORDER"
REJECT_REASON_DATA_DEP = "DATA_DEPENDENCY"
REJECT_REASON_CONTROL_CTX = "CONTROL_CONTEXT_MISMATCH"
REJECT_REASON_SIDE_EFFECT = "SIDE_EFFECT_OBV_CONFLICT"
REJECT_REASON_PARSE = "PARSE_FAILURE"


MUTATIVE_METHOD_KEYWORDS = {
    "add",
    "create",
    "delete",
    "remove",
    "update",
    "edit",
    "save",
    "send",
    "pay",
    "complete",
    "cancel",
    "post",
    "put",
    "transfer",
    "subscribe",
    "unsubscribe",
    "follow",
    "unfollow",
    "mark",
    "archive",
    "restore",
    "login",
    "logout",
    "reply",
    "reply_to",
}


SPLIT_ORDER_HINT_RE = re.compile(
    r"\b(first|then|after|before|step\s+1|stepwise|in order|prioritize)\b",
    re.IGNORECASE,
)


def read_json(path: Path) -> Any:
    with path.open("r") as f:
        return json.load(f)


def read_text(path: Path) -> str:
    with path.open("r") as f:
        return f.read()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--appworld-root",
        default=environ.get("CERTCF_APPWORLD_ROOT", str(Path(__file__).resolve().parents[1] / "data/appworld")),
        help="Path containing AppWorld data/. Defaults to CERTCF_APPWORLD_ROOT or ./data/appworld.",
    )
    parser.add_argument(
        "--split",
        action="append",
        default=["train", "dev"],
        help="Dataset split(s) to scan. Repeat for multiple splits.",
    )
    parser.add_argument(
        "--out-jsonl",
        default="artifacts/phase0_6/appworld_candidates.jsonl",
        help="Where to write pair-level records.",
    )
    parser.add_argument(
        "--max-sample",
        type=int,
        default=10,
        help="Deterministic number of examples for each candidate/rejected sample.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=7,
        help="Seed for deterministic sample selection.",
    )
    return parser.parse_args()


def get_control_names(node: ast.AST) -> set[str]:
    names: set[str] = set()

    def walk_names(expr: ast.AST | None) -> None:
        if expr is None:
            return
        for sub in ast.walk(expr):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                names.add(sub.id)

    if isinstance(node, ast.If):
        walk_names(node.test)
    elif isinstance(node, ast.While):
        walk_names(node.test)
    elif isinstance(node, ast.AsyncFor) or isinstance(node, ast.For):
        walk_names(node.iter)
        if isinstance(node.target, ast.Name):
            names.discard(node.target.id)
    elif isinstance(node, ast.With) or isinstance(node, ast.AsyncWith):
        for item in node.items:
            walk_names(item.context_expr)
            if item.optional_vars:
                for sub in ast.walk(item.optional_vars):
                    if isinstance(sub, ast.Name):
                        names.discard(sub.id)
    elif isinstance(node, ast.Try):
        pass
    elif isinstance(node, ast.ExceptHandler):
        walk_names(node.type)
    return names


def node_signature(node: ast.AST) -> str:
    if isinstance(node, ast.If):
        return f"if:{ast.unparse(node.test)}"
    if isinstance(node, ast.While):
        return f"while:{ast.unparse(node.test)}"
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return f"for:{ast.unparse(node.target)} in {ast.unparse(node.iter)}"
    if isinstance(node, (ast.With, ast.AsyncWith)):
        inner = ", ".join(ast.unparse(item.context_expr) for item in node.items)
        return f"with:{inner}"
    if isinstance(node, ast.Try):
        return "try"
    if isinstance(node, ast.ExceptHandler):
        if node.type is None:
            return "except"
        return f"except:{ast.unparse(node.type)}"
    return ast.dump(node)


def assign_targets_from_nodes(nodes: Sequence[ast.AST]) -> set[str]:
    out: set[str] = set()
    for node in nodes:
        for target in ast.walk(node):
            if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Store):
                out.add(target.id)
    return out


def names_in_expr(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
            names.add(sub.id)
    return names


def is_api_call(node: ast.Call) -> bool:
    if not isinstance(node.func, ast.Attribute):
        return False
    path = get_attr_chain(node.func)
    parts = path.split(".")
    return len(parts) >= 3 and parts[0] == "apis"


def get_attr_chain(node: ast.AST) -> str:
    if not isinstance(node, ast.Attribute):
        return ""
    if isinstance(node.value, ast.Attribute):
        parent = get_attr_chain(node.value)
        if parent:
            return f"{parent}.{node.attr}"
        return node.attr
    if isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return node.attr


def get_api_parts(node: ast.Call) -> tuple[str, str, str] | None:
    path = get_attr_chain(node.func)
    if not path:
        return None
    parts = path.split(".")
    if len(parts) < 3 or parts[0] != "apis":
        return None
    app = parts[1]
    method = parts[2]
    return path, app, method


def infer_receiver_name(node: ast.Call) -> str | None:
    if not isinstance(node.func, ast.Attribute):
        return None
    current: ast.AST = node.func
    while isinstance(current, ast.Attribute):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def is_mutative(method: str) -> bool:
    method_l = method.lower()
    for token in MUTATIVE_METHOD_KEYWORDS:
        if token in method_l.split("_"):
            return True
        if method_l.startswith(token):
            return True
    return False


def has_task_order_hint(text: str | None) -> bool:
    if not text:
        return False
    return bool(SPLIT_ORDER_HINT_RE.search(text))


def collect_api_calls(source: str, split: str, task_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    events: list[dict[str, Any]] = []
    parse_errors: list[str] = []

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        parse_errors.append(f"{exc}")
        return events, parse_errors

    solution_fn = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "solution":
            solution_fn = node
            break
    if solution_fn is None:
        parse_errors.append("solution() function missing")
        return events, parse_errors

    parent: dict[ast.AST, ast.AST] = {}
    stack: list[ast.AST] = [solution_fn]
    for node in ast.walk(solution_fn):
        if isinstance(node, ast.AST):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.AST):
                    parent[child] = node

    call_nodes: list[tuple[int, int, ast.Call]] = []
    for node in ast.walk(solution_fn):
        if not isinstance(node, ast.Call):
            continue
        if not is_api_call(node):
            continue
        lineno = getattr(node, "lineno", 0)
        col = getattr(node, "col_offset", 0)
        call_nodes.append((lineno, col, node))
    call_nodes.sort(key=lambda t: (t[0], t[1]))

    for idx, (_, __, call) in enumerate(call_nodes, start=1):
        info = get_api_parts(call)
        if info is None:
            continue
        callee, app, method = info
        assigned_to: set[str] = set()
        current: ast.AST | None = call
        while current is not None and current in parent:
            p = parent[current]
            if isinstance(p, ast.Assign) and isinstance(p.value, ast.Call) and p.value is current:
                assigned_to |= assign_targets_from_nodes(p.targets)
            elif isinstance(p, ast.AnnAssign) and isinstance(p.value, ast.Call) and p.value is current:
                assigned_to |= assign_targets_from_nodes([p.target])
            current = p

        receiver = infer_receiver_name(call)
        arg_names = names_in_expr(
            ast.Tuple(elts=[*call.args, *[kw.value for kw in call.keywords if kw.value is not None]], ctx=ast.Load())
        )  # type: ignore[arg-type]

        control_frames: list[str] = []
        control_names: set[str] = set()
        cur: ast.AST | None = call
        while cur is not None:
            par = parent.get(cur)
            if par is None:
                break
            in_body = False
            if isinstance(par, ast.If):
                if cur in par.body:
                    control_frames.append(f"if:{ast.unparse(par.test)}:body")
                    control_names |= get_control_names(par)
                elif cur in par.orelse:
                    control_frames.append(f"if:{ast.unparse(par.test)}:orelse")
                    control_names |= get_control_names(par)
            elif isinstance(par, ast.While):
                control_frames.append(f"while:{ast.unparse(par.test)}")
                control_names |= get_control_names(par)
            elif isinstance(par, (ast.For, ast.AsyncFor)):
                if cur in par.body:
                    control_frames.append(f"{node_signature(par)}:body")
                    control_names |= get_control_names(par)
                elif cur in par.orelse:
                    control_frames.append(f"{node_signature(par)}:orelse")
                    control_names |= get_control_names(par)
            elif isinstance(par, (ast.With, ast.AsyncWith)):
                control_frames.append(f"{node_signature(par)}:{'body' if cur in par.body else 'other'}")
                control_names |= get_control_names(par)
            elif isinstance(par, ast.Try):
                if cur in par.body:
                    control_frames.append("try:body")
                elif cur in par.orelse:
                    control_frames.append("try:orelse")
                elif any(cur in h.body for h in par.handlers):
                    for handler in par.handlers:
                        if cur in handler.body:
                            control_frames.append(f"except:{ast.unparse(handler.type) if handler.type else '*'}:body")
                control_names |= get_control_names(par)
            elif isinstance(par, ast.ExceptHandler):
                control_frames.append(f"except:{ast.unparse(par.type) if par.type else '*'}")
                control_names |= get_control_names(par)
            cur = par

        source_snippet = ast.get_source_segment(source, call)
        events.append(
            {
                "index": idx,
                "task_id": task_id,
                "split": split,
                "callee": callee,
                "app": app,
                "method": method,
                "receiver_name": receiver,
                "assigned_to": sorted(assigned_to),
                "arg_names": sorted(arg_names),
                "control_signature": tuple(control_frames),
                "control_names": sorted(control_names),
                "lineno": getattr(call, "lineno", 0),
                "col_offset": getattr(call, "col_offset", 0),
                "source": source_snippet.strip() if source_snippet else ast.unparse(call),
            }
        )

    return events, parse_errors


def evaluate_pair(event_a: dict[str, Any], event_b: dict[str, Any], task_order_sensitive: bool) -> OrderRequirementResult:
    reasons: list[str] = []

    if task_order_sensitive:
        reasons.append(REJECT_REASON_TASK_ORDER)

    a_assigned = set(event_a["assigned_to"])
    b_assigned = set(event_b["assigned_to"])
    a_arg_names = set(event_a["arg_names"])
    b_arg_names = set(event_b["arg_names"])
    a_control_names = set(event_a["control_names"])
    b_control_names = set(event_b["control_names"])

    if a_assigned.intersection(b_arg_names):
        reasons.append(REJECT_REASON_DATA_DEP)
    if b_assigned.intersection(a_arg_names):
        reasons.append(REJECT_REASON_DATA_DEP)

    if event_a["receiver_name"] and event_a["receiver_name"] in b_arg_names:
        reasons.append(REJECT_REASON_DATA_DEP)
    if event_b["receiver_name"] and event_b["receiver_name"] in a_arg_names:
        reasons.append(REJECT_REASON_DATA_DEP)

    if set(event_a["control_signature"]) != set(event_b["control_signature"]):
        # Different explicit control scopes => cannot guarantee same execution context.
        reasons.append(REJECT_REASON_CONTROL_CTX)
    if a_control_names.intersection(b_assigned):
        reasons.append(REJECT_REASON_DATA_DEP)
    if b_control_names.intersection(a_assigned):
        reasons.append(REJECT_REASON_DATA_DEP)

    same_app = event_a["app"] == event_b["app"] and event_a["app"] is not None
    if same_app:
        if is_mutative(event_a["method"]) or is_mutative(event_b["method"]):
            reasons.append(REJECT_REASON_SIDE_EFFECT)

    status = "REJECTED"
    if not reasons:
        status = "POTENTIAL_CANDIDATE"

    return {
        "status": status,
        "reasons": sorted(set(reasons)),
        "dependency_summary": {
            "a_assigned_to": sorted(a_assigned),
            "b_assigned_to": sorted(b_assigned),
            "a_arg_names": sorted(a_arg_names),
            "b_arg_names": sorted(b_arg_names),
            "a_control_names": sorted(a_control_names),
            "b_control_names": sorted(b_control_names),
        },
    }


def scan_task(appworld_data: Path, split: str, task_id: str) -> tuple[dict[str, Any], list[OrderRequirementResult]]:
    result: dict[str, Any] = {
        "split": split,
        "task_id": task_id,
        "status": "parsed",
        "num_api_calls": 0,
        "num_candidate_pairs": 0,
        "num_rejected_pairs": 0,
        "candidate_pairs": [],
        "rejected_pairs": [],
    }

    task_dir = appworld_data / "tasks" / task_id
    gt_dir = task_dir / "ground_truth"
    specs_file = task_dir / "specs.json"
    metadata_file = gt_dir / "metadata.json"
    compiled_file = gt_dir / "compiled_solution.py"

    if not (task_dir.exists() and gt_dir.exists() and compiled_file.exists() and metadata_file.exists()):
        result["status"] = "missing_task_artifacts"
        return result, []

    metadata = read_json(metadata_file)
    if metadata.get("mode", "full") != "full":
        result["status"] = "non_full_ground_truth"
        return result, []

    specs = read_json(specs_file) if specs_file.exists() else {}
    instruction = specs.get("instruction") if isinstance(specs, dict) else ""
    task_order_sensitive = has_task_order_hint(instruction)

    source = read_text(compiled_file)
    events, parse_errors = collect_api_calls(source, split, task_id)
    if parse_errors:
        result["status"] = "parse_failure"
        result["parse_errors"] = parse_errors
        return result, []

    if len(events) < 2:
        result["status"] = "insufficient_calls"
        return result, []

    result["status"] = "scanned"
    result["num_api_calls"] = len(events)

    ordered_events = events
    ordered_events.sort(key=lambda e: (e["lineno"], e["col_offset"], e["index"]))
    task_records: list[OrderRequirementResult] = []

    for i, event_a in enumerate(ordered_events):
        for j in range(i + 1, len(ordered_events)):
            event_b = ordered_events[j]
            outcome = evaluate_pair(event_a, event_b, task_order_sensitive=task_order_sensitive)
            pair_record = {
                "task_id": task_id,
                "split": split,
                "i": event_a["index"],
                "j": event_b["index"],
                "a": {k: event_a[k] for k in ["lineno", "col_offset", "callee", "source", "app", "method", "arg_names", "assigned_to", "control_signature"]},
                "b": {k: event_b[k] for k in ["lineno", "col_offset", "callee", "source", "app", "method", "arg_names", "assigned_to", "control_signature"]},
                **outcome,
            }
            task_records.append(pair_record)
            if outcome["status"] == "POTENTIAL_CANDIDATE":
                result["num_candidate_pairs"] += 1
                result["candidate_pairs"].append(pair_record)
            else:
                result["num_rejected_pairs"] += 1
                result["rejected_pairs"].append(pair_record)

    return result, task_records


def main() -> None:
    args = parse_args()
    appworld_root = Path(args.appworld_root)
    appworld_data = appworld_root / "data"

    out_jsonl = Path(args.out_jsonl)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)

    summary = {
        "version_info": {},
        "total_train_tasks": 0,
        "total_dev_tasks": 0,
        "tasks_scanned": 0,
        "tasks_with_parse": 0,
        "tasks_with_full_gt": 0,
        "tasks_with_at_least_2_api_calls": 0,
        "candidate_task_count": 0,
        "potential_candidate_pair_count": 0,
        "rejection_reason_counts": Counter(),
        "reject_reason_by_task": Counter(),
        "candidate_call_app_distribution": Counter(),
        "sample_candidate_pairs": [],
        "sample_rejected_pairs": [],
        "task_level": [],
    }

    split_paths = {
        "train": appworld_data / "datasets" / "train.txt",
        "dev": appworld_data / "datasets" / "dev.txt",
    }
    splits = args.split

    all_records: list[dict[str, Any]] = []
    for split in splits:
        dataset_file = split_paths.get(split)
        if dataset_file is None or not dataset_file.exists():
            raise FileNotFoundError(f"Dataset file not found: {dataset_file}")
        task_ids = [line.strip() for line in dataset_file.read_text().splitlines() if line.strip()]
        if split == "train":
            summary["total_train_tasks"] = len(task_ids)
        if split == "dev":
            summary["total_dev_tasks"] = len(task_ids)

        for task_id in task_ids:
            summary["tasks_scanned"] += 1
            task_summary, records = scan_task(appworld_data, split, task_id)
            summary["task_level"].append(task_summary)
            if task_summary["status"] in {"scanned"}:
                summary["tasks_with_parse"] += 1
                summary["tasks_with_full_gt"] += 1
                if task_summary["num_api_calls"] >= 2:
                    summary["tasks_with_at_least_2_api_calls"] += 1
                if task_summary["num_candidate_pairs"]:
                    summary["candidate_task_count"] += 1
                summary["potential_candidate_pair_count"] += task_summary["num_candidate_pairs"]
                for rec in records:
                    all_records.append(rec)
                    if rec["status"] == "POTENTIAL_CANDIDATE":
                        summary["candidate_call_app_distribution"][rec["a"]["app"]] += 1
                        summary["candidate_call_app_distribution"][rec["b"]["app"]] += 1
                    else:
                        for reason in rec["reasons"]:
                            summary["rejection_reason_counts"][reason] += 1
            else:
                if task_summary["status"] == "parse_failure":
                    summary["rejection_reason_counts"][REJECT_REASON_PARSE] += 1
                    summary["reject_reason_by_task"][REJECT_REASON_PARSE] += 1

    # Keep deterministic samples.
    random.seed(args.seed)
    candidate_records = [r for r in all_records if r["status"] == "POTENTIAL_CANDIDATE"]
    rejected_records = [r for r in all_records if r["status"] != "POTENTIAL_CANDIDATE"]
    sample_n = args.max_sample
    if len(candidate_records) > sample_n:
        candidate_records = random.sample(candidate_records, sample_n)
    if len(rejected_records) > sample_n:
        rejected_records = random.sample(rejected_records, sample_n)
    summary["sample_candidate_pairs"] = candidate_records
    summary["sample_rejected_pairs"] = rejected_records

    with out_jsonl.open("w") as f:
        for rec in all_records:
            f.write(json.dumps(rec, ensure_ascii=False))
            f.write("\n")

    # Convert Counters to stable dicts for logging/serialization.
    summary_payload = {
        "total_train_tasks": summary["total_train_tasks"],
        "total_dev_tasks": summary["total_dev_tasks"],
        "tasks_scanned": summary["tasks_scanned"],
        "tasks_with_parse": summary["tasks_with_parse"],
        "tasks_with_full_gt": summary["tasks_with_full_gt"],
        "tasks_with_at_least_2_api_calls": summary["tasks_with_at_least_2_api_calls"],
        "potential_candidate_task_count": summary["candidate_task_count"],
        "potential_candidate_pair_count": summary["potential_candidate_pair_count"],
        "rejection_reason_counts": dict(summary["rejection_reason_counts"]),
        "candidate_call_app_distribution": dict(summary["candidate_call_app_distribution"]),
        "out_jsonl": str(out_jsonl),
        "num_records_written": len(all_records),
        "sample_candidate_count": len(summary["sample_candidate_pairs"]),
        "sample_rejected_count": len(summary["sample_rejected_pairs"]),
    }

    print(json.dumps(summary_payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
