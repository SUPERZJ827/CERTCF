#!/usr/bin/env python3
"""Static opportunity scan for R2 literal-constrained argument sensitivity."""

from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from agentdojo.functions_runtime import FunctionCall
from agentdojo.task_suite.load_suites import get_suite, get_suites

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "phase2_0"
REFERENCE = ROOT / "reference" / "agentdojo"
BENCHMARK_VERSION = "v1.2.1"
COMMIT = "357c80dea9af34323f709c3505d9e6d224654c7e"
NUMBER_TOKEN = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?![\w.])")
DATE_TOKEN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EMAIL_TOKEN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_TOKEN = re.compile(r"^\+?[\d() .-]{7,}$")


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def canonical_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", round_trip=True)
    if isinstance(value, dict):
        return {str(key): canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, FunctionCall):
        return {"__derived_function_call__": canonical_value(value)}
    raise TypeError(type(value).__name__)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(canonical_value(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def call_data(call: FunctionCall) -> dict[str, Any]:
    return {"function": call.function, "arguments": canonical_value(call.args), "id": call.id, "placeholder_args": canonical_value(call.placeholder_args) if call.placeholder_args is not None else None}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n")


def literal_occurrences(prompt: str, value: Any) -> tuple[int, str | None, str]:
    normalized_prompt = normalize_text(prompt)
    if isinstance(value, bool) or value is None:
        return 0, None, "unsupported_literal_type"
    if isinstance(value, (int, float)):
        try:
            target = Decimal(str(value))
        except InvalidOperation:
            return 0, None, "invalid_numeric"
        matches = []
        for token in NUMBER_TOKEN.findall(normalized_prompt):
            try:
                if Decimal(token) == target:
                    matches.append(token)
            except InvalidOperation:
                pass
        return len(matches), matches[0] if matches else None, "numeric_exact"
    if not isinstance(value, str) or not value.strip():
        return 0, None, "unsupported_literal_type"
    target = normalize_text(value)
    pattern = re.compile(rf"(?<!\w){re.escape(target)}(?!\w)")
    matches = list(pattern.finditer(normalized_prompt))
    return len(matches), target if matches else None, "nfkc_whitespace_casefold_exact"


def argument_type(name: str, value: Any, schema: dict) -> str:
    enum_values = schema.get("enum")
    if enum_values:
        return "enum"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "monetary_float" if any(word in name.casefold() for word in ("amount", "price", "cost", "budget")) else "float"
    if isinstance(value, str):
        if DATE_TOKEN.fullmatch(value):
            return "date"
        if EMAIL_TOKEN.fullmatch(value):
            return "email"
        if PHONE_TOKEN.fullmatch(value):
            return "phone"
        if any(word in name.casefold() for word in ("city", "location", "address")):
            return "location_string"
        if any(word in name.casefold() for word in ("id", "account", "recipient", "user", "sender")):
            return "string_identifier"
        return "exact_string"
    return "structured_or_other"


def tool_uses_argument(tool: Any, argument_name: str) -> tuple[bool | None, dict[str, Any] | None]:
    try:
        source = inspect.getsource(tool.run)
        tree = ast.parse(source)
        function = next(node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
        used = any(isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == argument_name for node in ast.walk(function))
        return used, {"file": inspect.getsourcefile(tool.run), "line": inspect.getsourcelines(tool.run)[1]}
    except (OSError, TypeError, SyntaxError, StopIteration):
        return None, None


def schema_property(tool: Any, name: str) -> dict:
    return tool.parameters.model_json_schema().get("properties", {}).get(name, {})


def schema_valid(tool: Any, arguments: dict[str, Any]) -> bool:
    try:
        tool.parameters.model_validate(arguments)
        return True
    except Exception:
        return False


def ground_truth_input(environment: Any) -> Any:
    """Adapt the one upstream GT-only date representation mismatch."""
    pre_environment = environment.model_copy(deep=True)
    calendar = getattr(pre_environment, "calendar", None)
    current_day = getattr(calendar, "current_day", None)
    if isinstance(current_day, str) and DATE_TOKEN.fullmatch(current_day):
        calendar = calendar.model_copy(update={"current_day": date.fromisoformat(current_day)})
        pre_environment = pre_environment.model_copy(update={"calendar": calendar})
    return pre_environment


def proposed_mutation(tool: Any, name: str, value: Any, kind: str, arguments: dict[str, Any], corpus: dict[tuple[str, str, str], set[bytes]], suite_name: str, schema: dict) -> tuple[Any | None, str | None, str | None]:
    replacement: Any | None = None
    rule: str | None = None
    if kind == "integer":
        replacement, rule = value + 1, "integer_plus_one"
    elif kind in ("float", "monetary_float") and value > 0:
        replacement, rule = value + 0.01, "positive_float_plus_0_01"
    elif kind == "date":
        try:
            replacement, rule = (date.fromisoformat(value) + timedelta(days=1)).isoformat(), "iso_date_plus_one_day"
        except ValueError:
            return None, None, "value_not_safely_perturbable"
    elif schema.get("enum"):
        alternatives = sorted((item for item in schema["enum"] if canonical_bytes(item) != canonical_bytes(value)), key=canonical_bytes)
        if alternatives:
            replacement, rule = alternatives[0], "lexicographically_smallest_different_schema_enum"
    elif isinstance(value, str):
        alternatives = sorted((json.loads(item) for item in corpus[(suite_name, tool.name, name)] if item != canonical_bytes(value)), key=canonical_bytes)
        if alternatives:
            replacement, rule = alternatives[0], "lexicographically_smallest_different_official_gt_value_for_same_suite_function_argument"
    if replacement is None or canonical_bytes(replacement) == canonical_bytes(value):
        return None, None, "value_not_safely_perturbable"
    modified = copy.deepcopy(arguments)
    modified[name] = replacement
    if not schema_valid(tool, modified):
        return None, None, "tool_schema_uncertainty"
    return replacement, rule, None


def main() -> None:
    suite_inventory = {
        suite_name: list(suite.user_tasks)
        for suite_name, suite in get_suites(BENCHMARK_VERSION).items()
    }
    tasks: list[dict[str, Any]] = []
    corpus: dict[tuple[str, str, str], set[bytes]] = defaultdict(set)
    total_calls = 0
    total_arguments = 0
    for suite_name, task_ids in suite_inventory.items():
        for task_id in task_ids:
            suite = get_suite(BENCHMARK_VERSION, suite_name)
            task = suite.get_user_task_by_id(task_id)
            tools = {tool.name: tool for tool in suite.tools}
            environment = task.init_environment(suite.load_and_inject_default_environment({}))
            gt = task.ground_truth(ground_truth_input(environment))
            total_calls += len(gt)
            total_arguments += sum(len(call.args) for call in gt)
            task_record = {"suite": suite_name, "task_id": task_id, "task_version": list(suite.benchmark_version), "prompt": task.PROMPT, "prompt_digest": digest(task.PROMPT), "ground_truth": [call_data(call) for call in gt], "ground_truth_digest": digest([call_data(call) for call in gt]), "tools": tools, "calls": gt}
            tasks.append(task_record)
            for call in gt:
                for name, value in call.args.items():
                    if not isinstance(value, FunctionCall):
                        try:
                            corpus[(suite_name, call.function, name)].add(canonical_bytes(value))
                        except TypeError:
                            pass

    candidates = []
    rejected = []
    exact_matches = 0
    rejection_counts = Counter()
    for task in tasks:
        for call_index, call in enumerate(task["calls"]):
            tool = task["tools"].get(call.function)
            for argument_name, value in call.args.items():
                base = {"suite": task["suite"], "task_id": task["task_id"], "task_version": task["task_version"], "prompt_digest": task["prompt_digest"], "ground_truth_digest": task["ground_truth_digest"], "call_index": call_index, "function": call.function, "argument_name": argument_name, "argument_path": f"$.calls[{call_index}].arguments.{argument_name}"}
                if isinstance(value, FunctionCall):
                    reason = "derived_argument"
                    rejection_counts[reason] += 1
                    rejected.append({**base, "rejection_reason": reason})
                    continue
                try:
                    serialized_value = canonical_value(value)
                except TypeError:
                    reason = "other"
                    rejection_counts[reason] += 1
                    rejected.append({**base, "rejection_reason": reason})
                    continue
                occurrences, matched_literal, normalization = literal_occurrences(task["prompt"], serialized_value)
                if occurrences == 0:
                    reason = "no_literal_grounding"
                    rejection_counts[reason] += 1
                    rejected.append({**base, "original_value": serialized_value, "rejection_reason": reason})
                    continue
                if occurrences != 1:
                    reason = "ambiguous_grounding"
                    rejection_counts[reason] += 1
                    rejected.append({**base, "original_value": serialized_value, "match_count": occurrences, "rejection_reason": reason})
                    continue
                exact_matches += 1
                if tool is None:
                    reason = "tool_schema_uncertainty"
                    rejection_counts[reason] += 1
                    rejected.append({**base, "original_value": serialized_value, "rejection_reason": reason})
                    continue
                schema = schema_property(tool, argument_name)
                kind = argument_type(argument_name, serialized_value, schema)
                used, location = tool_uses_argument(tool, argument_name)
                if used is not True:
                    reason = "semantic_importance_unknown"
                    rejection_counts[reason] += 1
                    rejected.append({**base, "original_value": serialized_value, "argument_type": kind, "source_implementation": location, "rejection_reason": reason})
                    continue
                replacement, rule, error = proposed_mutation(tool, argument_name, serialized_value, kind, canonical_value(call.args), corpus, task["suite"], schema)
                if error:
                    rejection_counts[error] += 1
                    rejected.append({**base, "original_value": serialized_value, "argument_type": kind, "source_implementation": location, "rejection_reason": error})
                    continue
                candidate = {**base, "status": "POTENTIAL_R2_LITERAL_CANDIDATE", "original_value": serialized_value, "original_value_type": type(value).__name__, "argument_type": kind, "matched_prompt_literal": matched_literal, "prompt_match_count": occurrences, "normalization_rule": normalization, "proposed_value": replacement, "mutation_rule": rule, "tool_schema": schema, "complete_modified_arguments_schema_valid": True, "tool_argument_used_in_implementation": True, "source_implementation": location, "semantic_violation_certified": False}
                candidate["candidate_id"] = hashlib.sha256(canonical_bytes({key: candidate[key] for key in ("suite", "task_id", "ground_truth_digest", "call_index", "function", "argument_name", "original_value", "proposed_value")})).hexdigest()[:20]
                candidates.append(candidate)

    candidates.sort(key=lambda record: record["candidate_id"])
    with (OUT / "r2_literal_candidates.jsonl").open("w") as handle:
        for candidate in candidates:
            handle.write(json.dumps(candidate, ensure_ascii=True, sort_keys=True) + "\n")
    write_json(OUT / "r2_rejection_summary.json", {"counts": dict(sorted(rejection_counts.items())), "examples": rejected[:50]})
    candidate_tasks = {(record["suite"], record["task_id"]) for record in candidates}
    type_distribution = Counter(record["argument_type"] for record in candidates)
    suite_distribution = Counter(record["suite"] for record in candidates)
    gate = "R2_READY_FOR_CALIBRATION_PILOT" if len(candidate_tasks) >= 10 and len(type_distribution) >= 2 else ("R2_CONDITIONAL_SMALL_CALIBRATION" if 5 <= len(candidate_tasks) <= 9 else "R2_INSUFFICIENT_OPPORTUNITY")
    results = {"agentdojo_commit": COMMIT, "benchmark_version": BENCHMARK_VERSION, "total_tasks": len(tasks), "total_gt_calls": total_calls, "total_gt_arguments": total_arguments, "exact_prompt_literal_matches": exact_matches, "candidate_arguments": len(candidates), "candidate_tasks": len(candidate_tasks), "argument_type_distribution": dict(sorted(type_distribution.items())), "suite_distribution": dict(sorted(suite_distribution.items())), "rejection_counts": dict(sorted(rejection_counts.items())), "gate": gate, "perturbed_trajectory_executions": 0, "perturbed_evaluator_executions": 0, "perturbed_evaluator_outcomes_observed": 0, "llm_api_calls": 0, "mutation_rules_sha256": hashlib.sha256((OUT / "R2_MUTATION_RULES_V1.json").read_bytes()).hexdigest(), "scanner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    write_json(OUT / "results.json", results)
    report = f"""# Phase 2.0: R2 Literal-Constrained Argument Sensitivity Gate

## Scope

AgentDojo is used as an R2 calibration target. This phase statically scanned the complete official `{BENCHMARK_VERSION}` user-task population and called `ground_truth()` only to obtain structured official calls. It did not execute a tool call, perturbed trajectory, or evaluator and did not use an LLM.

## Frozen hypothesis and prospective certificate

R2 changes exactly one deterministically grounded ground-truth argument to a schema-valid value under the frozen type-specific mutation rule. A future case may receive `SEMANTIC_VIOLATION_CERTIFIED` only when the prompt explicitly requires literal L, original GT uses normalized L, transformed execution uses a different value in the unchanged target tool call, every other argument/call remains unchanged, execution succeeds, and source/runtime evidence confirms that the tool actually consumes the perturbed argument. Final-state difference alone is insufficient.

## Static accounting

| Metric | Count |
|---|---:|
| Official user tasks | {results['total_tasks']} |
| Ground-truth calls | {results['total_gt_calls']} |
| Ground-truth top-level arguments | {results['total_gt_arguments']} |
| Exact prompt-literal matches | {results['exact_prompt_literal_matches']} |
| POTENTIAL_R2_LITERAL_CANDIDATE arguments | {results['candidate_arguments']} |
| Candidate tasks | {results['candidate_tasks']} |

Argument types:

```json
{json.dumps(results['argument_type_distribution'], indent=2, sort_keys=True)}
```

Suite distribution:

```json
{json.dumps(results['suite_distribution'], indent=2, sort_keys=True)}
```

## Rejections

```json
{json.dumps(results['rejection_counts'], indent=2, sort_keys=True)}
```

`no_literal_grounding` includes concrete GT values that do not occur as a unique normalized literal in the prompt. `ambiguous_grounding` rejects repeated matching literals. `derived_argument` covers nested FunctionCall values. Schema failures, unsupported safe mutations, and source-level argument-use uncertainty remain excluded rather than inferred.

## Gate

`{gate}`

No candidate is called a certified semantic violation. No perturbation or evaluator outcome has been observed. This phase stops at static opportunity discovery.
"""
    (ROOT / "PHASE2_0_R2_LITERAL_SENSITIVITY_GATE.md").write_text(report)


if __name__ == "__main__":
    main()
