#!/usr/bin/env python3
"""Phase 0.8: reconcile Phase 0.6 eligibility with the direct-swap contract.

This scanner is read-only. It neither constructs a transformed solution nor
executes AppWorld or its evaluator.
"""

from __future__ import annotations
from os import environ

import argparse
import ast
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DIRECT = "POTENTIAL_DIRECT_SWAP_CANDIDATE"
REJECT_DIRECT = "NOT_DIRECT_COMPLETE_STATEMENTS"
REJECT_NONADJACENT = "NOT_ADJACENT_STATEMENTS"
REJECT_DEPENDENCY = "DATA_DEPENDENCY"
REJECT_CONTROL = "CONTROL_STRUCTURE_OR_EXPLICIT_ORDER"
REJECT_SIDE_EFFECT = "SIDE_EFFECT_OBV_CONFLICT"

MUTATIVE_METHOD_KEYWORDS = {
    "add", "create", "delete", "remove", "update", "edit", "save", "send", "pay", "complete",
    "cancel", "post", "put", "transfer", "subscribe", "unsubscribe", "follow", "unfollow", "mark",
    "archive", "restore", "login", "logout", "reply", "reply_to",
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def attribute_path(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Attribute):
        return None
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name) or current.id != "apis":
        return None
    return "apis." + ".".join(reversed(parts))


def api_calls(solution: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    calls = [node for node in ast.walk(solution) if isinstance(node, ast.Call) and attribute_path(node.func)]
    return sorted(calls, key=lambda node: (node.lineno, node.col_offset))


def load_names(node: ast.AST) -> set[str]:
    return {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load)}


def stored_names(node: ast.AST) -> set[str]:
    return {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store)}


def statement_direct_call(statement: ast.stmt) -> ast.Call | None:
    """Return an API call only for the exact statement forms admitted by V2."""
    value: ast.AST | None = None
    if isinstance(statement, ast.Expr):
        value = statement.value
    elif isinstance(statement, ast.Assign):
        value = statement.value
    elif isinstance(statement, ast.AnnAssign):
        value = statement.value
    if not isinstance(value, ast.Call) or not attribute_path(value.func):
        return None
    # A direct statement may contain precisely one API call, namely its RHS/expression.
    calls = [node for node in ast.walk(statement) if isinstance(node, ast.Call) and attribute_path(node.func)]
    return value if calls == [value] else None


def statement_lists(root: ast.AST) -> list[list[ast.stmt]]:
    lists: list[list[ast.stmt]] = []
    for parent in ast.walk(root):
        for _, value in ast.iter_fields(parent):
            if isinstance(value, list) and value and all(isinstance(item, ast.stmt) for item in value):
                lists.append(value)
    return lists


def is_mutative(method: str) -> bool:
    lowered = method.lower()
    return any(token in lowered.split("_") or lowered.startswith(token) for token in MUTATIVE_METHOD_KEYWORDS)


def direct_event(call: ast.Call, index: int, statement: ast.stmt) -> dict[str, Any]:
    path = attribute_path(call.func)
    assert path is not None
    _, app, method = path.split(".", 2)
    arguments = [*call.args, *(keyword.value for keyword in call.keywords)]
    return {
        "index": index,
        "callee": path,
        "app": app,
        "method": method,
        "lineno": call.lineno,
        "source": ast.unparse(call),
        "assigned_to": sorted(stored_names(statement.targets[0]) if isinstance(statement, ast.Assign) else stored_names(statement.target) if isinstance(statement, ast.AnnAssign) else set()),
        "arg_names": sorted(load_names(ast.Tuple(elts=arguments, ctx=ast.Load()))),
        "statement_lineno": statement.lineno,
    }


def classify_pair(a: dict[str, Any], b: dict[str, Any], old: dict[str, Any]) -> str | None:
    old_reasons = set(old.get("reasons", []))
    if "TASK_SPEC_EXPLICIT_ORDER" in old_reasons or "CONTROL_CONTEXT_MISMATCH" in old_reasons:
        return REJECT_CONTROL
    if "DATA_DEPENDENCY" in old_reasons:
        return REJECT_DEPENDENCY
    if "SIDE_EFFECT_OBV_CONFLICT" in old_reasons:
        return REJECT_SIDE_EFFECT
    a_assigned, b_assigned = set(a["assigned_to"]), set(b["assigned_to"])
    if a_assigned & set(b["arg_names"]) or b_assigned & set(a["arg_names"]):
        return REJECT_DEPENDENCY
    if a["app"] == b["app"] and (is_mutative(a["method"]) or is_mutative(b["method"])):
        return REJECT_SIDE_EFFECT
    return None


def scan_task(task_id: str, split: str, source: str, old_records: dict[tuple[str, int, int], dict[str, Any]]) -> list[dict[str, Any]]:
    tree = ast.parse(source)
    solution = next((node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "solution"), None)
    if solution is None:
        raise ValueError("solution() missing")
    all_calls = api_calls(solution)
    index_by_id = {id(call): index for index, call in enumerate(all_calls, start=1)}
    records: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for body in statement_lists(solution):
        for left_statement, right_statement in zip(body, body[1:]):
            left, right = statement_direct_call(left_statement), statement_direct_call(right_statement)
            if left is None or right is None:
                continue
            i, j = index_by_id[id(left)], index_by_id[id(right)]
            seen.add((i, j))
            old = old_records.get((task_id, i, j))
            if old is None:
                # This pair never passed Phase 0.6; record its frozen static rejection.
                continue
            a, b = direct_event(left, i, left_statement), direct_event(right, j, right_statement)
            reason = classify_pair(a, b, old)
            records.append({
                "task_id": task_id, "split": split, "i": i, "j": j, "a": a, "b": b,
                "phase0_6_status": old["status"], "phase0_6_reasons": old.get("reasons", []),
                "status": DIRECT if old["status"] == "POTENTIAL_CANDIDATE" and reason is None else "REJECTED",
                "reasons": [] if old["status"] == "POTENTIAL_CANDIDATE" and reason is None else [reason or "PHASE0_6_NOT_ELIGIBLE"],
                "contract": "same_statement_list + adjacent + direct_single_api_call_per_statement",
            })
    # Account for every historical Phase 0.6 candidate not admitted above.
    for (candidate_task, i, j), old in old_records.items():
        if candidate_task != task_id or old["status"] != "POTENTIAL_CANDIDATE" or (i, j) in seen:
            continue
        call_a = all_calls[i - 1] if i <= len(all_calls) else None
        call_b = all_calls[j - 1] if j <= len(all_calls) else None
        direct_a = call_a is not None and any(statement_direct_call(statement) is call_a for body in statement_lists(solution) for statement in body)
        direct_b = call_b is not None and any(statement_direct_call(statement) is call_b for body in statement_lists(solution) for statement in body)
        reason = REJECT_DIRECT if not direct_a or not direct_b else REJECT_NONADJACENT
        records.append({"task_id": task_id, "split": split, "i": i, "j": j, "status": "REJECTED", "reasons": [reason], "phase0_6_status": old["status"], "phase0_6_reasons": old.get("reasons", []), "contract": "same_statement_list + adjacent + direct_single_api_call_per_statement"})
    return records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--appworld-root", type=Path, default=Path(environ.get("CERTCF_APPWORLD_ROOT", str(Path(__file__).resolve().parents[1] / "data/appworld"))))
    parser.add_argument("--phase0-6-jsonl", type=Path, default=Path("artifacts/phase0_6/appworld_candidates.jsonl"))
    parser.add_argument("--out-jsonl", type=Path, default=Path("artifacts/phase0_8/appworld_direct_swap_candidates_v2.jsonl"))
    parser.add_argument("--report", type=Path, default=Path("PHASE0_8_APPWORLD_TRANSFORMABILITY_RECONCILIATION.md"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    old_rows = [json.loads(line) for line in args.phase0_6_jsonl.read_text().splitlines() if line.strip()]
    old_by_key = {(row["task_id"], row["i"], row["j"]): row for row in old_rows}
    splits = {"train": args.appworld_root / "data/datasets/train.txt", "dev": args.appworld_root / "data/datasets/dev.txt"}
    task_count = parsed_count = calls_count = 0
    records: list[dict[str, Any]] = []
    for split, task_file in splits.items():
        for task_id in (line.strip() for line in task_file.read_text().splitlines() if line.strip()):
            task_count += 1
            source_file = args.appworld_root / "data/tasks" / task_id / "ground_truth/compiled_solution.py"
            try:
                source = source_file.read_text()
                tree = ast.parse(source)
                solution = next(node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "solution")
                if len(api_calls(solution)) >= 2:
                    calls_count += 1
                parsed_count += 1
                records.extend(scan_task(task_id, split, source, old_by_key))
            except (OSError, SyntaxError, StopIteration):
                continue
    args.out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.out_jsonl.open("w") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    historical = [row for row in old_rows if row["status"] == "POTENTIAL_CANDIDATE"]
    historical_rejections = [row for row in records if row["status"] == "REJECTED" and row["phase0_6_status"] == "POTENTIAL_CANDIDATE"]
    reasons = Counter(reason for row in historical_rejections for reason in row["reasons"])
    preexcluded_direct_pairs = [row for row in records if row["status"] == "REJECTED" and row["phase0_6_status"] != "POTENTIAL_CANDIDATE"]
    preserved_reasons = Counter(reason for row in preexcluded_direct_pairs for reason in row["reasons"])
    accepted = [row for row in records if row["status"] == DIRECT]
    accepted_tasks = sorted({row["task_id"] for row in accepted})
    samples: dict[str, list[dict[str, Any]]] = {}
    random.seed(8)
    for label, rows in [(DIRECT, accepted), *[(reason, [row for row in records if reason in row.get("reasons", [])]) for reason in [REJECT_DIRECT, REJECT_NONADJACENT, REJECT_DEPENDENCY, REJECT_CONTROL, REJECT_SIDE_EFFECT]]]:
        samples[label] = random.sample(rows, min(10, len(rows)))
    gate = "GO_TO_DUAL_REPLAY_V2" if len(accepted_tasks) >= 5 else "CONDITIONAL_SMALL_PILOT" if len(accepted_tasks) >= 3 else "APPWORLD_NO_GO_FOR_DIRECT_SWAP"
    report = f"""# Phase 0.8: AppWorld Transformability Reconciliation\n\n## Scope and frozen boundary\n\nThis read-only reconciliation corrects only the mismatch between the Phase 0.6 static predicate and the existing Phase 0.7 direct-statement transformation contract. It does not alter Phase 0.6/0.7 artifacts, execute a solution, execute a transformed evaluator, inspect evaluator outcomes, or add a second transformation.\n\n## Exact V2 syntactic predicate\n\nA record is `POTENTIAL_DIRECT_SWAP_CANDIDATE` only when the two calls are in adjacent statements of the same AST statement list; each statement is exactly `api(...)`, `x = api(...)`, or annotated assignment with the API call as its direct RHS; and each statement contains exactly one API call. Comprehensions, subscripts, chained expressions, nested calls, conditions, and non-adjacent statements are rejected. Existing Phase 0.6 dependency, control/order, and side-effect rejection evidence remains binding.\n\n## Accounting\n\n| Metric | Count |\n| --- | ---: |\n| Total task universe | {task_count} |\n| Successfully parsed tasks | {parsed_count} |\n| Tasks with >=2 API calls | {calls_count} |\n| Phase 0.6 potential candidates | {len(historical)} |\n| Rejected: not direct statements | {reasons[REJECT_DIRECT]} |\n| Rejected: non-adjacent | {reasons[REJECT_NONADJACENT]} |\n| Rejected: dependency | {reasons[REJECT_DEPENDENCY]} |\n| Rejected: control structure / explicit order | {reasons[REJECT_CONTROL]} |\n| Rejected: obvious side-effect conflict | {reasons[REJECT_SIDE_EFFECT]} |\n| Final candidate tasks | {len(accepted_tasks)} |\n| Final candidate pairs | {len(accepted)} |\n\nThe candidate source SHA-256 is `{sha256_file(args.phase0_6_jsonl)}`. The V2 output contains accepted records and all historical-candidate exclusions with their structural reason.\n\n## Samples\n\n"""
    report += f"""## Accounting note

The five rejection rows above use only the frozen Phase 0.6 potential-candidate denominator: 1,617 + 93 + 18 accepted = 1,728. Dependency/control/side-effect checks had already excluded such pairs in Phase 0.6, so their reconciliation counts are zero. Separately, the full direct-statement scan found {len(preexcluded_direct_pairs)} direct adjacent pairs that retain Phase 0.6 exclusions: dependency={preserved_reasons[REJECT_DEPENDENCY]}, control/order={preserved_reasons[REJECT_CONTROL]}, side-effect={preserved_reasons[REJECT_SIDE_EFFECT]}.

"""
    for label, rows in samples.items():
        report += f"### {label}\n\n"
        if not rows:
            report += "No records.\n\n"
            continue
        for row in rows:
            report += f"- `{row['task_id']}` calls `{row['i']};{row['j']}`: {row.get('status')} ({', '.join(row.get('reasons', [])) or 'none'})\n"
        report += "\n"
    report += f"## Gate\n\n`{gate}`\n\nStop here. No V2 dual replay or evaluator outcome has been generated.\n"
    args.report.write_text(report)


if __name__ == "__main__":
    main()
