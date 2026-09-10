#!/usr/bin/env python3
"""Audit R2 viability, select the deterministic pilot, and freeze it."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from agentdojo.task_suite.load_suites import get_suite

from phase1_3_agentdojo_common import BENCHMARK_VERSION, COMMIT, ROOT, canonical_bytes, canonical_value, file_digest, write_json

OUT = ROOT / "artifacts" / "phase2_1"
SOURCE = ROOT / "artifacts" / "phase2_0" / "r2_literal_candidates.jsonl"
RULES = ROOT / "artifacts" / "phase2_0" / "R2_MUTATION_RULES_V1.json"
EXPECTED_SOURCE_SHA256 = "8d636e4c29435c45bf97d4bca8e0cb95412f68b7245d9e2d533706828a37ae33"
RULE_VERSION = "R2_MUTATION_RULES_V1"
QUOTAS = {
    "integer": 1,
    "monetary_float": 1,
    "date": 2,
    "string_identifier": 2,
    "location_string": 2,
    "exact_string": 2,
}
FALLBACK = ("date", "string_identifier", "location_string", "exact_string")

# These arguments denote existing entities. Their deterministic replacement must
# occur in the concrete task's official initial environment.
ENVIRONMENT_BACKED_ARGUMENTS = {
    ("slack", "add_user_to_channel", "user"),
    ("slack", "add_user_to_channel", "channel"),
    ("slack", "read_channel_messages", "channel"),
    ("slack", "read_inbox", "user"),
    ("slack", "send_channel_message", "channel"),
    ("slack", "send_direct_message", "recipient"),
    ("travel", "get_all_car_rental_companies_in_city", "city"),
    ("travel", "get_all_hotels_in_city", "city"),
    ("travel", "get_all_restaurants_in_city", "city"),
    ("travel", "get_hotels_address", "hotel_name"),
    ("workspace", "search_files_by_filename", "filename"),
}


def normalize(value: Any) -> Any:
    if isinstance(value, str):
        import unicodedata

        return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    return value


def scalar_leaves(value: Any):
    value = canonical_value(value)
    if isinstance(value, dict):
        for item in value.values():
            yield from scalar_leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from scalar_leaves(item)
    else:
        yield value


def call_data(call: Any) -> dict[str, Any]:
    return {"function": call.function, "arguments": canonical_value(call.args), "id": call.id, "placeholder_args": canonical_value(call.placeholder_args) if call.placeholder_args is not None else None}


def load_source() -> list[dict[str, Any]]:
    if file_digest(SOURCE) != EXPECTED_SOURCE_SHA256:
        raise RuntimeError("Phase 2.0 candidate artifact digest mismatch")
    return [json.loads(line) for line in SOURCE.read_text().splitlines() if line.strip()]


def schema_valid(tool: Any, arguments: dict[str, Any]) -> bool:
    try:
        tool.parameters.model_validate(arguments)
        return True
    except Exception:
        return False


def selection_digest(record: dict[str, Any]) -> str:
    material = "".join(
        (
            record["suite"],
            record["task_id"],
            str(record["call_index"]),
            record["argument_name"],
            canonical_bytes(record["original_value"]).decode(),
            RULE_VERSION,
        )
    )
    return hashlib.sha256(material.encode()).hexdigest()


def audit_candidate(record: dict[str, Any]) -> dict[str, Any]:
    suite = get_suite(BENCHMARK_VERSION, record["suite"])
    task = suite.get_user_task_by_id(record["task_id"])
    environment = task.init_environment(suite.load_and_inject_default_environment({}))
    ground_truth = task.ground_truth(environment.model_copy(deep=True))
    if record["call_index"] >= len(ground_truth):
        return {**record, "viability_status": "MUTATION_VIABILITY_UNKNOWN", "viability_reason": "ground_truth_index_out_of_range"}
    call = ground_truth[record["call_index"]]
    if call.function != record["function"] or record["argument_name"] not in call.args:
        return {**record, "viability_status": "MUTATION_VIABILITY_UNKNOWN", "viability_reason": "ground_truth_mapping_mismatch"}
    original = canonical_value(call.args[record["argument_name"]])
    if canonical_bytes(original) != canonical_bytes(record["original_value"]):
        return {**record, "viability_status": "MUTATION_VIABILITY_UNKNOWN", "viability_reason": "ground_truth_value_mismatch"}

    occurrences = sum(normalize(value) == normalize(original) for gt_call in ground_truth for value in scalar_leaves(gt_call.args))
    if occurrences != 1:
        return {**record, "viability_status": "REJECTED", "viability_reason": "REDUNDANT_LITERAL_GROUNDING", "ground_truth_literal_occurrences": occurrences}

    compensation = [
        {"call_index": index, "function": gt_call.function, "argument_name": name}
        for index, gt_call in enumerate(ground_truth)
        for name, value in gt_call.args.items()
        if not (index == record["call_index"] and name == record["argument_name"])
        and any(normalize(leaf) == normalize(original) for leaf in scalar_leaves(value))
    ]
    if compensation:
        return {**record, "viability_status": "REJECTED", "viability_reason": "COMPENSATION_UNKNOWN", "possible_compensation": compensation}

    tool = next((item for item in suite.tools if item.name == call.function), None)
    if tool is None:
        return {**record, "viability_status": "MUTATION_VIABILITY_UNKNOWN", "viability_reason": "tool_schema_unavailable"}
    modified_arguments = canonical_value(call.args)
    modified_arguments[record["argument_name"]] = record["proposed_value"]
    if not schema_valid(tool, modified_arguments):
        return {**record, "viability_status": "MUTATION_VIABILITY_UNKNOWN", "viability_reason": "replacement_fails_official_schema"}
    if type(original) is not type(record["proposed_value"]):
        return {**record, "viability_status": "MUTATION_VIABILITY_UNKNOWN", "viability_reason": "declared_python_type_changed"}
    if canonical_bytes(original) == canonical_bytes(record["proposed_value"]):
        return {**record, "viability_status": "MUTATION_VIABILITY_UNKNOWN", "viability_reason": "replacement_not_distinct"}

    key = (record["suite"], record["function"], record["argument_name"])
    environment_match_count = sum(normalize(value) == normalize(record["proposed_value"]) for value in scalar_leaves(environment))
    if key in ENVIRONMENT_BACKED_ARGUMENTS and environment_match_count == 0:
        return {**record, "viability_status": "MUTATION_VIABILITY_UNKNOWN", "viability_reason": "replacement_not_in_official_initial_environment", "environment_match_count": 0}

    evidence_class = "official_environment_entity" if key in ENVIRONMENT_BACKED_ARGUMENTS else "official_schema_and_source_nonrejecting_argument"
    audited = {
        **record,
        "viability_status": "MUTATION_VIABLE",
        "viability_reason": None,
        "ground_truth_literal_occurrences": occurrences,
        "anti_compensation_check": "PASS",
        "replacement_differs": True,
        "replacement_python_type_preserved": True,
        "complete_modified_arguments_schema_valid": True,
        "replacement_environment_match_count": environment_match_count,
        "replacement_legality_evidence_class": evidence_class,
        "selection_digest": selection_digest(record),
        "official_ground_truth": [call_data(item) for item in ground_truth],
        "prompt": task.PROMPT,
    }
    return audited


def select_pilot(viable: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_type: dict[str, list[dict[str, Any]]] = {}
    for argument_type in QUOTAS:
        by_type[argument_type] = sorted((record for record in viable if record["argument_type"] == argument_type), key=lambda record: record["selection_digest"])
    full = [record for argument_type in QUOTAS for record in by_type[argument_type]]
    selected: list[dict[str, Any]] = []
    used_tasks: set[tuple[str, str]] = set()
    selected_ids: set[str] = set()

    def take(argument_type: str, count: int) -> int:
        taken = 0
        for record in by_type[argument_type]:
            task_key = (record["suite"], record["task_id"])
            if record["candidate_id"] in selected_ids or task_key in used_tasks:
                continue
            selected.append(record)
            selected_ids.add(record["candidate_id"])
            used_tasks.add(task_key)
            taken += 1
            if taken == count:
                break
        return taken

    missing = 0
    for argument_type, quota in QUOTAS.items():
        missing += quota - take(argument_type, quota)
    while missing:
        progressed = False
        for argument_type in FALLBACK:
            if take(argument_type, 1):
                missing -= 1
                progressed = True
                if not missing:
                    break
        if not progressed:
            break
    return full, selected


def main() -> None:
    receipt_path = OUT / "FREEZE_RECEIPT.json"
    if receipt_path.exists():
        raise FileExistsError("Phase 2.1 freeze receipt already exists")
    records = load_source()
    if len(records) != 104:
        raise RuntimeError("Phase 2.0 candidate count changed")
    audited = [audit_candidate(record) for record in records]
    viable = [record for record in audited if record["viability_status"] == "MUTATION_VIABLE"]
    full, pilot = select_pilot(viable)
    if len(pilot) != 10 or len({(record["suite"], record["task_id"]) for record in pilot}) != 10:
        raise RuntimeError("Unable to construct the frozen ten-task stratified pilot")

    write_json(OUT / "R2_VIABILITY_AUDIT_V1.json", audited)
    write_json(OUT / "R2_VIABLE_POPULATION_V1.json", viable)
    write_json(OUT / "R2_PILOT_QUEUE_FULL_V1.json", full)
    write_json(OUT / "R2_PILOT_QUEUE_V1.json", pilot)
    (OUT / "MUTATION_EXACTNESS_SPECIFICATION_V1.txt").write_text("Change exactly one frozen top-level argument of one frozen FunctionCall. Preserve trajectory length, call order, function identities, target position, every non-target call, and every non-target argument. The new value must equal the frozen deterministic proposed value and differ from the uniquely grounded original literal.\n")
    (OUT / "SEMANTIC_VIOLATION_SPECIFICATION_V1.txt").write_text("Certification requires unique explicit prompt grounding, unique official-GT grounding, exact one-argument mutation, successful target execution using the replacement, no compensating call using the original literal, at least one changed observable response/downstream response/final state/final output, and no grounding, schema, execution, or compensation uncertainty.\n")
    (OUT / "EVALUATOR_RELATION_SPECIFICATION_V1.txt").write_text("For an official original baseline that passes, a SEMANTIC_VIOLATION_CERTIFIED perturbation is SENSITIVE iff transformed official utility fails; a transformed pass is only EVALUATOR_FALSE_ACCEPTANCE_CANDIDATE. No uncertified case enters the sensitivity denominator.\n")
    (OUT / "VIABILITY_SPECIFICATION_V1.txt").write_text("Use only frozen Phase 2.0 replacements. Require unique normalized literal occurrence across all GT arguments, no possible compensating GT argument, identical Python value type, complete official schema validation, and source-backed successful-input semantics. Existing-entity arguments additionally require the replacement as a scalar in the fresh official initial environment.\n")

    runner = ROOT / "scripts" / "phase2_1_agentdojo_run.py"
    if not runner.exists():
        raise FileNotFoundError(runner)
    receipt = {
        "agentdojo_repository_url": "https://github.com/sequrity-ai/agentdojo",
        "agentdojo_commit": COMMIT,
        "agentdojo_package_version": "0.1.34",
        "benchmark_version": BENCHMARK_VERSION,
        "phase2_0_candidate_sha256": file_digest(SOURCE),
        "mutation_rules_sha256": file_digest(RULES),
        "mutation_viability_implementation_sha256": file_digest(Path(__file__)),
        "viability_specification_sha256": file_digest(OUT / "VIABILITY_SPECIFICATION_V1.txt"),
        "viable_population_sha256": file_digest(OUT / "R2_VIABLE_POPULATION_V1.json"),
        "full_queue_sha256": file_digest(OUT / "R2_PILOT_QUEUE_FULL_V1.json"),
        "pilot_queue_sha256": file_digest(OUT / "R2_PILOT_QUEUE_V1.json"),
        "execution_runner_sha256": file_digest(runner),
        "instrumentation_sha256": file_digest(ROOT / "scripts" / "phase1_3_agentdojo_common.py"),
        "adapter_sha256": file_digest(ROOT / "scripts" / "phase1_3_retry1_adapter.py"),
        "mutation_exactness_specification_sha256": file_digest(OUT / "MUTATION_EXACTNESS_SPECIFICATION_V1.txt"),
        "semantic_violation_specification_sha256": file_digest(OUT / "SEMANTIC_VIOLATION_SPECIFICATION_V1.txt"),
        "evaluator_relation_sha256": file_digest(OUT / "EVALUATOR_RELATION_SPECIFICATION_V1.txt"),
        "state_canonicalization_sha256": file_digest(ROOT / "artifacts/phase1_2/STATE_CANONICALIZATION_V1.txt"),
        "response_canonicalization_sha256": file_digest(ROOT / "artifacts/phase1_2/RESPONSE_CANONICALIZATION_V1.txt"),
        "viability_counts": dict(Counter(record["viability_status"] for record in audited)),
        "viability_rejection_reasons": dict(Counter(record.get("viability_reason") for record in audited if record.get("viability_reason"))),
        "viable_candidates": len(viable),
        "viable_tasks": len({(record["suite"], record["task_id"]) for record in viable}),
        "pilot_size": len(pilot),
        "pilot_argument_types": dict(Counter(record["argument_type"] for record in pilot)),
        "python_version": sys.version,
        "platform": platform.platform(),
        "dependency_versions": {name: importlib.metadata.version(name) for name in ("pydantic", "docstring-parser", "deepdiff", "pyyaml")},
        "perturbed_trajectory_executions_before_freeze": 0,
        "perturbed_evaluator_outcomes_before_freeze": 0,
        "candidate_selection_manual": False,
        "mutation_selection_manual": False,
        "mutation_rules_changed_after_outcome": False,
        "evaluator_relation_changed": False,
        "llm_api_calls": 0,
        "issue_pr_search_performed": False,
    }
    write_json(receipt_path, receipt)


if __name__ == "__main__":
    main()
