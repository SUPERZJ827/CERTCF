#!/usr/bin/env python3
"""Static/source/original-only opportunity scan for R2A effect sensitivity."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from agentdojo.task_suite.load_suites import get_suite, get_suites

import phase1_3_agentdojo_run as protocol
from phase1_3_agentdojo_common import BENCHMARK_VERSION, COMMIT, ROOT, call_data, canonical_bytes, canonical_value, digest, write_json
from phase1_3_retry1_agentdojo_run import retry1_evaluate
from phase2_0_scan_r2_literals import argument_type, ground_truth_input, literal_occurrences, normalize_text, schema_property

OUT = ROOT / "artifacts" / "phase2_3"
STATUS = "POTENTIAL_R2A_EFFECT_CANDIDATE"
ACTION_PREFIXES = ("create_", "send_", "transfer_", "update_", "modify_", "delete_", "schedule_", "book_", "move_", "add_", "remove_", "cancel_", "reschedule_", "append_", "share_", "post_")
DEVELOPMENT_TASKS = {
    ("banking", "user_task_8"),
    ("banking", "user_task_15"),
    ("workspace", "user_task_12"),
    ("workspace", "user_task_6"),
    ("slack", "user_task_4"),
    ("slack", "user_task_7"),
    ("travel", "user_task_14"),
    ("travel", "user_task_2"),
    ("workspace", "user_task_14"),
    ("banking", "user_task_5"),
}

# Direct argument-to-persistent-effect mappings established from official tools.
CAUSAL_FIELDS = {
    ("create_calendar_event", "title"): ("calendar_event", "title"),
    ("create_calendar_event", "description"): ("calendar_event", "description"),
    ("create_file", "filename"): ("cloud_drive_file", "filename"),
    ("send_email", "subject"): ("sent_email", "subject"),
    ("send_channel_message", "channel"): ("channel_message", "recipient"),
    ("send_channel_message", "body"): ("channel_message", "body"),
    ("send_direct_message", "recipient"): ("direct_message", "recipient"),
    ("send_direct_message", "body"): ("direct_message", "body"),
    ("send_money", "recipient"): ("transaction", "recipient"),
    ("send_money", "amount"): ("transaction", "amount"),
    ("schedule_transaction", "recipient"): ("scheduled_transaction", "recipient"),
    ("schedule_transaction", "amount"): ("scheduled_transaction", "amount"),
    ("update_password", "password"): ("user_account", "password"),
    ("update_user_info", "street"): ("user_account", "street"),
    ("update_user_info", "city"): ("user_account", "city"),
    ("share_file", "email"): ("file_share_relation", "email"),
    ("add_user_to_channel", "user"): ("channel_membership", "user"),
}


def direct_role_evidence(prompt: str, function: str, name: str, literal: Any) -> dict[str, Any] | None:
    if not isinstance(literal, (str, int, float)) or isinstance(literal, bool):
        return None
    text, value = normalize_text(prompt), normalize_text(str(literal))
    escaped = re.escape(value)
    patterns: list[tuple[str, str]] = []
    if name == "description":
        patterns = [(rf"description(?: should be)?\s+['\"]?{escaped}", "explicit_description_cue")]
    elif name == "title":
        patterns = [(rf"titled\s+['\"]?{escaped}", "explicit_title_cue")]
    elif name == "filename":
        patterns = [(rf"file named\s+['\"]?{escaped}", "explicit_filename_cue")]
    elif name == "subject":
        patterns = [(rf"subject(?: of the email)? should be\s+['\"]?{escaped}", "explicit_subject_cue")]
    elif name == "password":
        patterns = [(rf"password to\s+['\"]?{escaped}", "explicit_password_cue")]
    elif name == "recipient":
        patterns = [
            (rf"(?:send|message|write)(?:\s+\w+){{0,8}}\s+to\s+{escaped}\b", "explicit_recipient_cue"),
            (rf"refund\s+{escaped}\b", "explicit_refund_recipient_cue"),
            (rf"recipient is\s+{escaped}\b", "explicit_recipient_field_cue"),
        ]
    elif name == "channel":
        patterns = [(rf"(?:to|the)\s+['\"]?{escaped}['\"]?\s+channel\b", "explicit_channel_cue")]
    elif name == "body":
        patterns = [
            (rf"following (?:question|message):\s*['\"]?{escaped}", "explicit_message_body_cue"),
            (rf"write them the following message:\s*['\"]?{escaped}", "explicit_message_body_cue"),
        ]
    elif name == "email":
        patterns = [(rf"share(?:\s+\w+){{0,8}}\s+with\s+{escaped}\b", "explicit_share_recipient_cue")]
    elif name == "amount":
        patterns = [(rf"(?:refund|send|transfer)(?:\s+\w+){{0,8}}\s+{escaped}\b", "explicit_amount_cue")]
    for pattern, rule in patterns:
        matches = list(re.finditer(pattern, text))
        if len(matches) == 1:
            return {"rule": rule, "normalized_match": matches[0].group(), "semantic_role": name}
    return None


def flatten(value: Any, path: str = "$"):
    value = canonical_value(value)
    if isinstance(value, dict):
        for key, item in value.items():
            yield from flatten(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from flatten(item, f"{path}[{index}]")
    else:
        yield path, value


def schema_valid(tool: Any, arguments: dict[str, Any]) -> bool:
    try:
        tool.parameters.model_validate(arguments)
        return True
    except Exception:
        return False


def replacement(record: dict[str, Any], corpus: dict[tuple[str, str, str], set[bytes]], tool: Any) -> tuple[Any | None, str | None]:
    value, kind = record["literal"], record["argument_type"]
    proposed: Any | None = None
    rule: str | None = None
    if kind == "integer":
        proposed, rule = value + 1, "integer_plus_one"
    elif kind in ("float", "monetary_float") and value > 0:
        proposed, rule = value + 0.01, "positive_float_plus_0_01"
    elif kind == "date":
        from datetime import date, timedelta

        proposed, rule = (date.fromisoformat(value) + timedelta(days=1)).isoformat(), "iso_date_plus_one_day"
    else:
        alternatives = sorted((json.loads(item) for item in corpus[(record["suite"], record["function"], record["argument_name"])] if item != canonical_bytes(value)), key=canonical_bytes)
        if alternatives:
            proposed, rule = alternatives[0], "smallest_different_official_gt_value_same_function_argument"
        elif record["function"] == "create_file" and record["argument_name"] == "filename" and isinstance(value, str):
            stem, dot, suffix = value.rpartition(".")
            proposed = f"{stem}-r2a.{suffix}" if dot else f"{value}-r2a"
            rule = "filename_insert_r2a_before_extension"
        elif record["function"] == "update_password" and record["argument_name"] == "password" and isinstance(value, str):
            proposed, rule = value + "-r2a", "password_append_r2a"
    if proposed is None or canonical_bytes(proposed) == canonical_bytes(value) or type(proposed) is not type(value):
        return None, None
    arguments = dict(record["complete_arguments"])
    arguments[record["argument_name"]] = proposed
    return (proposed, rule) if schema_valid(tool, arguments) else (None, None)


def effect_witness(initial: Any, final: Any, record: dict[str, Any]) -> dict[str, Any] | None:
    initial_flat = dict(flatten(initial))
    value = record["literal"]
    field = record["state_effect_field"]
    witnesses = []
    for path, observed in flatten(final):
        if path.rsplit(".", 1)[-1] != field:
            continue
        if canonical_bytes(observed) != canonical_bytes(value):
            continue
        if path not in initial_flat or canonical_bytes(initial_flat[path]) != canonical_bytes(observed):
            witnesses.append({"field_path": path, "original_value": observed, "initial_value": initial_flat.get(path), "state_object": record["state_effect_entity"]})
    if witnesses:
        return sorted(witnesses, key=lambda item: item["field_path"])[0]
    if record["function"] == "add_user_to_channel" and record["argument_name"] == "user":
        user = str(value)
        initial_channels = canonical_value(initial).get("slack", {}).get("user_channels", {}).get(user, [])
        final_channels = canonical_value(final).get("slack", {}).get("user_channels", {}).get(user, [])
        if initial_channels != final_channels:
            return {"field_path": f"$.slack.user_channels.{user}", "entity_identity": user, "original_value": user, "initial_relation": initial_channels, "final_relation": final_channels, "state_object": "channel_membership"}
    if record["function"] == "share_file" and record["argument_name"] == "email":
        for path, item in flatten(final):
            if path.endswith(f".shared_with.{value}") and initial_flat.get(path) != item:
                return {"field_path": path, "entity_identity": value, "original_value": value, "initial_value": initial_flat.get(path), "final_value": item, "state_object": "file_share_relation"}
    return None


def main() -> None:
    if (OUT / "results.json").exists() or (OUT / "r2a_effect_candidates.jsonl").exists():
        raise FileExistsError("Phase 2.3 outputs already exist")
    inventory = {name: list(suite.user_tasks) for name, suite in get_suites(BENCHMARK_VERSION).items()}
    task_records: list[dict[str, Any]] = []
    corpus: dict[tuple[str, str, str], set[bytes]] = defaultdict(set)
    total_calls = total_arguments = 0
    for suite_name, task_ids in inventory.items():
        for task_id in task_ids:
            suite = get_suite(BENCHMARK_VERSION, suite_name)
            task = suite.get_user_task_by_id(task_id)
            environment = task.init_environment(suite.load_and_inject_default_environment({}))
            ground_truth = task.ground_truth(ground_truth_input(environment))
            tools = {tool.name: tool for tool in suite.tools}
            total_calls += len(ground_truth)
            total_arguments += sum(len(call.args) for call in ground_truth)
            task_records.append({"suite": suite_name, "task_id": task_id, "task": task, "environment": environment, "ground_truth": ground_truth, "tools": tools})
            for call in ground_truth:
                for name, value in call.args.items():
                    if not hasattr(value, "function"):
                        try:
                            corpus[(suite_name, call.function, name)].add(canonical_bytes(value))
                        except TypeError:
                            pass

    rejections = Counter()
    preliminary: list[dict[str, Any]] = []
    original_cache: dict[tuple[str, str], dict[str, Any]] = {}
    all_stage_candidates = 0
    for task_record in task_records:
        suite_name, task_id = task_record["suite"], task_record["task_id"]
        prompt = task_record["task"].PROMPT
        gt = task_record["ground_truth"]
        for call_index, call in enumerate(gt):
            for argument_name, raw_value in call.args.items():
                if not call.function.startswith(ACTION_PREFIXES):
                    rejections["READ_ONLY_OR_INTERMEDIATE"] += 1
                    continue
                mapping = CAUSAL_FIELDS.get((call.function, argument_name))
                if mapping is None:
                    rejections["NO_CAUSAL_EFFECT_MAPPING"] += 1
                    continue
                if hasattr(raw_value, "function"):
                    rejections["AMBIGUOUS_LITERAL_ROLE"] += 1
                    continue
                value = canonical_value(raw_value)
                occurrences, matched, normalization = literal_occurrences(prompt, value)
                role = direct_role_evidence(prompt, call.function, argument_name, value)
                if occurrences != 1 or role is None:
                    rejections["AMBIGUOUS_LITERAL_ROLE"] += 1
                    continue
                same_effect_producers = [
                    (index, other.function, name)
                    for index, other in enumerate(gt)
                    for name, other_value in other.args.items()
                    if CAUSAL_FIELDS.get((other.function, name)) == mapping and canonical_bytes(other_value) == canonical_bytes(value)
                ]
                if len(same_effect_producers) != 1:
                    rejections["MULTIPLE_EFFECT_PRODUCERS"] += 1
                    continue
                tool = task_record["tools"].get(call.function)
                if tool is None:
                    rejections["EFFECT_CLASS_UNKNOWN"] += 1
                    continue
                source = inspect.getsource(tool.run)
                if argument_name not in source:
                    rejections["NO_CAUSAL_EFFECT_MAPPING"] += 1
                    continue
                base = {
                    "suite": suite_name,
                    "task_id": task_id,
                    "task_version": list(get_suite(BENCHMARK_VERSION, suite_name).benchmark_version),
                    "prompt": prompt,
                    "prompt_digest": digest(prompt),
                    "literal": value,
                    "matched_prompt_literal": matched,
                    "normalization_rule": normalization,
                    "semantic_role": role,
                    "target_call_index": call_index,
                    "target_call": call_data(call),
                    "target_argument": argument_name,
                    "function": call.function,
                    "argument_name": argument_name,
                    "argument_type": argument_type(argument_name, value, schema_property(tool, argument_name)),
                    "complete_arguments": canonical_value(call.args),
                    "tool_effect_class": "STATE_MUTATING",
                    "state_effect_entity": mapping[0],
                    "state_effect_field": mapping[1],
                    "causal_mapping_evidence": {"tool_source_file": inspect.getsourcefile(tool.run), "tool_source_line": inspect.getsourcelines(tool.run)[1], "argument_loaded_by_implementation": True, "mapped_persisted_field": mapping[1]},
                    "unique_producer_evidence": {"count": 1, "producer": same_effect_producers[0]},
                    "development_exposure": (suite_name, task_id) in DEVELOPMENT_TASKS,
                    "ground_truth_digest": digest([call_data(item) for item in gt]),
                }
                proposed, mutation_rule = replacement(base, corpus, tool)
                if proposed is None:
                    rejections["MUTATION_NOT_VIABLE"] += 1
                    continue
                base.update({"proposed_value": proposed, "deterministic_mutation_rule": mutation_rule, "mutation_schema_valid": True})
                all_stage_candidates += 1
                if base["development_exposure"]:
                    rejections["DEVELOPMENT_EXPOSED"] += 1
                    continue
                key = (suite_name, task_id)
                if key not in original_cache:
                    suite_o, task_o, trajectory_o, environment_o = protocol.fresh_context(base)
                    initial = environment_o.model_copy(deep=True)
                    execution = protocol.execute(suite_o, task_o, trajectory_o, environment_o)
                    evaluator = retry1_evaluate(suite_o, task_o, initial, execution) if execution["status"] == "SUCCESS" else {"official_verdict": False}
                    original_cache[key] = {"initial": initial, "execution": execution, "evaluator": evaluator}
                original = original_cache[key]
                if original["execution"]["status"] != "SUCCESS" or original["evaluator"].get("official_verdict") is not True:
                    rejections["NO_ORIGINAL_EFFECT_WITNESS"] += 1
                    continue
                witness = effect_witness(original["initial"], original["execution"]["final_state"], base)
                if witness is None:
                    rejections["NO_ORIGINAL_EFFECT_WITNESS"] += 1
                    continue
                base["original_effect_witness"] = {**witness, "final_state_digest": original["execution"]["final_state_digest"], "causal_source_call": {"call_index": call_index, "function": call.function, "argument": argument_name}}
                base["status"] = STATUS
                base["candidate_id"] = hashlib.sha256(canonical_bytes({key: base[key] for key in ("suite", "task_id", "ground_truth_digest", "target_call_index", "target_argument", "literal", "proposed_value")})).hexdigest()[:20]
                preliminary.append(base)

    preliminary.sort(key=lambda record: record["candidate_id"])
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "r2a_effect_candidates.jsonl").open("w") as handle:
        for record in preliminary:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
    candidate_tasks = {(record["suite"], record["task_id"]) for record in preliminary}
    effect_types = Counter(f"{record['state_effect_entity']}.{record['state_effect_field']}" for record in preliminary)
    argument_types = Counter(record["argument_type"] for record in preliminary)
    suite_distribution = Counter(record["suite"] for record in preliminary)
    task_population = {(record["suite"], record["task_id"]) for record in task_records}
    gate = "R2A_READY_FOR_HELDOUT_CALIBRATION" if len(candidate_tasks) >= 10 and len(argument_types) >= 2 else ("R2A_CONDITIONAL_SMALL_HELDOUT" if 5 <= len(candidate_tasks) <= 9 else "R2A_INSUFFICIENT_OPPORTUNITY")
    for key in ("READ_ONLY_OR_INTERMEDIATE", "AMBIGUOUS_LITERAL_ROLE", "NO_CAUSAL_EFFECT_MAPPING", "EFFECT_CLASS_UNKNOWN", "MULTIPLE_EFFECT_PRODUCERS", "NO_ORIGINAL_EFFECT_WITNESS", "MUTATION_NOT_VIABLE", "DEVELOPMENT_EXPOSED", "other"):
        rejections.setdefault(key, 0)
    results = {
        "relation": "R2A_TASK_CRITICAL_EFFECT_SENSITIVITY",
        "prior_r2_status": "EXPLORATORY_RELATION_REJECTED_FOR_LOW_SEMANTIC_PRECISION",
        "agentdojo_role": "CALIBRATION_TARGET",
        "agentdojo_commit": COMMIT,
        "benchmark_version": BENCHMARK_VERSION,
        "all_tasks": len(task_population),
        "development_exposed_tasks": len(DEVELOPMENT_TASKS),
        "previously_unexposed_tasks": len(task_population - DEVELOPMENT_TASKS),
        "total_gt_calls": total_calls,
        "total_gt_arguments": total_arguments,
        "all_pre_witness_candidates_including_development": all_stage_candidates,
        "heldout_candidate_arguments": len(preliminary),
        "heldout_candidate_tasks": len(candidate_tasks),
        "heldout_argument_type_distribution": dict(sorted(argument_types.items())),
        "heldout_effect_type_distribution": dict(sorted(effect_types.items())),
        "heldout_suite_distribution": dict(sorted(suite_distribution.items())),
        "original_gt_tasks_executed": len(original_cache),
        "original_effect_witness_available": len(preliminary),
        "rejection_counts": dict(sorted(rejections.items())),
        "candidate_artifact_sha256": hashlib.sha256((OUT / "r2a_effect_candidates.jsonl").read_bytes()).hexdigest(),
        "gate": gate,
        "r2a_perturbed_trajectory_executions": 0,
        "r2a_perturbed_evaluator_executions": 0,
        "llm_api_calls": 0,
        "issue_pr_search_performed": False,
    }
    write_json(OUT / "results.json", results)
    report = f"""# Phase 2.3: R2A Task-Critical Effect Sensitivity Gate

## Decision

`{gate}`

AgentDojo remains a `CALIBRATION_TARGET`. The previous R2 relation is frozen as `EXPLORATORY_RELATION_REJECTED_FOR_LOW_SEMANTIC_PRECISION` and its remaining population was not executed.

## R2A precision contract

R2A accepts only official state-mutating GT actions with a deterministic, argument-role-specific prompt cue; a direct tool-source mapping from that argument to a persistent effect; one unique effect producer; a successful official original replay; and a final-state effect witness equal to the literal. Read-only/intermediate calls and cross-role lexical matches are rejected. Phase 2.1 tasks are development-exposed and excluded from the held-out gate.

## Accounting

```json
{json.dumps(results, indent=2, sort_keys=True)}
```

## Methodological boundary

This phase executed official original GT trajectories only where needed to obtain effect witnesses. It executed no R2A perturbation or perturbed evaluator, used no LLM API, and performed no issue/PR search. Every accepted record remains only `{STATUS}`.
"""
    (ROOT / "PHASE2_3_R2A_EFFECT_SENSITIVITY_GATE.md").write_text(report)


if __name__ == "__main__":
    main()
