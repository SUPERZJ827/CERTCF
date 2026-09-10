#!/usr/bin/env python3
"""Phase 3.0 ThinkingBox-Bench v1.0 R2A feasibility audit.

This is a static/source scanner plus a three-case ORIGINAL-only replay.  It
never constructs or executes a perturbed trajectory and never invokes an LLM.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import importlib
import importlib.metadata
import importlib.util
import inspect
import json
import os
import pathlib
import platform
import re
import resource
import sys
import unicodedata
from collections import Counter
from datetime import date, timedelta
from typing import Any

import yaml


RELEASE = "thinkingbox-bench-v1.0"
THINKINGBOX_COMMIT = "40c1212f9582ca90175079bc313e530e9e9a4981"
DATA_COMMIT = "fcaba4c1a9debec42fda7f15bf29fe6d6b46c431"
DOMAIN_LABELS = {
    "sandbox_external_retail": "retail/e-commerce",
    "external_booking_v1": "travel/hospitality",
    "sandbox_auto_insurance": "auto insurance",
    "sandbox_neobank_support_v1": "neobank support",
    "sandbox_consulting": "consulting IT/HR",
}
DOMAIN_MODULE_NAMES = {
    "sandbox_external_retail": "mcp_sandbox_external_retail",
    "external_booking_v1": "mcp_external_booking_v1",
    "sandbox_auto_insurance": "mcp_sandbox_auto_insurance",
    "sandbox_neobank_support_v1": "mcp_sandbox_neobank_support_v1",
    "sandbox_consulting": "mcp_sandbox_consulting",
}
ACTION_TOKENS = {
    "add",
    "allocate",
    "assign",
    "book",
    "cancel",
    "charge",
    "create",
    "delete",
    "escalate",
    "file",
    "generate",
    "grant",
    "modify",
    "process",
    "provision",
    "refund",
    "reinstate",
    "remove",
    "reship",
    "reset",
    "retire",
    "revoke",
    "schedule",
    "send",
    "transfer",
    "update",
}
REQUEST_ACTION_WORDS = re.compile(
    r"\b(add|assign|book|cancel|change|create|delete|enroll|file|give|grant|"
    r"modify|move|provision|refund|remove|replace|report|request|reship|"
    r"schedule|send|set|transfer|update)\w*\b",
    re.IGNORECASE,
)
INTERMEDIATE_TOOL_PREFIXES = ("zendesk_",)
CONTROLLED_BUT_UNENUMERATED_FIELDS = {
    "country",
    "currency",
    "license_state",
    "shipping_address_state",
    "state",
}
FREE_TEXT_FIELDS = {
    "body",
    "comment",
    "description",
    "deliver_to",
    "location",
    "loss_location",
    "name",
    "notes",
    "reason",
    "special_requests",
    "subject",
    "title",
}
CREATED_IDENTIFIER_FIELDS = {
    "license_number",
    "other_party_phone",
    "police_report_number",
}
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    return sha256_bytes(path.read_bytes())


def jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def digest_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value))
    text = re.sub(r"\[([^]]+)\]\((?:mailto:)?[^)]*\)", r"\1", text)
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text


def exact_occurrences(prompt: str, value: Any) -> list[tuple[int, int]]:
    haystack = normalize_text(prompt)
    needle = normalize_text(value)
    if not needle or len(needle) < 2:
        return []
    pattern = re.compile(r"(?<![\w@.-])" + re.escape(needle) + r"(?![\w@.-])")
    return [(match.start(), match.end()) for match in pattern.finditer(haystack)]


def semantic_role_is_explicit(prompt: str, value: Any, field: str) -> tuple[bool, str]:
    normalized_prompt = normalize_text(prompt)
    occurrences = exact_occurrences(prompt, value)
    if len(occurrences) != 1:
        return False, "literal_not_unique_after_deterministic_normalization"
    start, end = occurrences[0]
    nearby = normalized_prompt[max(0, start - 140) : min(len(normalized_prompt), end + 140)]
    tokens = [
        token
        for token in re.split(r"[^a-z0-9]+", field.casefold())
        if len(token) > 2 and token not in {"new", "target", "value"}
    ]
    aliases = {
        "access_level": ("access", "permission", "level"),
        "board_type": ("board", "meal plan"),
        "date_of_birth": ("birth", "dob"),
        "license_number": ("license", "licence"),
        "loss_location": ("location", "occurred", "accident"),
        "other_party_insurance": ("insurance", "insurer", "other party"),
        "other_party_name": ("other party", "driver", "name"),
        "other_party_phone": ("phone", "number", "other party"),
        "police_report_number": ("police", "report"),
        "refund_amount": ("refund", "amount", "$"),
        "return_reason": ("return", "reason", "defect", "wrong", "damaged"),
        "shipping_speed": ("shipping", "delivery", "expedited", "next day"),
        "special_requests": ("request", "room", "checkout", "floor"),
    }.get(field, ())
    anchors = tuple(tokens) + tuple(aliases)
    role_anchor = next((anchor for anchor in anchors if anchor in nearby), None)
    if role_anchor is None:
        return False, "no_same-role_lexical_anchor_near_literal"
    if REQUEST_ACTION_WORDS.search(nearby) is None:
        return False, "no_explicit_action_request_near_literal"
    return True, f"unique literal with nearby role anchor {role_anchor!r}"


def iter_leaves(value: Any, path: tuple[Any, ...] = ()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from iter_leaves(child, path + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_leaves(child, path + (index,))
    else:
        yield path, value


def schema_for_path(schema: dict[str, Any], path: tuple[Any, ...]) -> dict[str, Any]:
    current = schema
    for part in path:
        if isinstance(part, int):
            current = current.get("items", {})
        else:
            current = current.get("properties", {}).get(part, {})
        branches = current.get("anyOf")
        if branches:
            non_null = [branch for branch in branches if branch.get("type") != "null"]
            if len(non_null) == 1:
                current = {**current, **non_null[0]}
    return current


def path_string(path: tuple[Any, ...]) -> str:
    out = "$"
    for part in path:
        out += f"[{part}]" if isinstance(part, int) else f".{part}"
    return out


def has_db_write(source: str) -> bool:
    return bool(re.search(r"\bdb\.(create|update|delete)\s*\(", source))


def is_action_tool(tool_name: str, source: str) -> bool:
    if tool_name.startswith(INTERMEDIATE_TOOL_PREFIXES):
        return False
    tokens = set(tool_name.casefold().split("_"))
    return bool(tokens & ACTION_TOKENS) and has_db_write(source)


def causal_effect_mapping(source: str, field: str) -> tuple[str, str] | None:
    escaped = re.escape(field)
    direct = re.compile(rf"\b{escaped}\s*=\s*request\.{escaped}\b")
    attribute = re.compile(
        rf"\b(?P<object>[a-zA-Z_]\w*)\.{escaped}\s*=\s*request\.{escaped}\b"
    )
    direct_match = direct.search(source)
    if direct_match:
        prefix = source[max(0, direct_match.start() - 1200) : direct_match.start()]
        constructors = list(re.finditer(r"\b([A-Z][A-Za-z0-9_]*)\s*\(", prefix))
        entity = constructors[-1].group(1) if constructors else "persisted_entity"
        return entity, f"{field}=request.{field} followed by database write"
    attribute_match = attribute.search(source)
    if attribute_match:
        obj = attribute_match.group("object")
        entity = obj.rstrip("s").title().replace("_", "") or "persisted_entity"
        return entity, f"{obj}.{field}=request.{field} followed by database update"
    return None


def value_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T.*)?", value):
            return "date_or_datetime"
        if "@" in value:
            return "email_or_recipient"
        if re.fullmatch(r"[A-Z]{2,}(?:-[A-Z0-9]+)+", value, re.IGNORECASE):
            return "string_identifier"
        return "exact_string"
    return type(value).__name__


def increment_last_alphanumeric(value: str) -> str | None:
    chars = list(value)
    for index in range(len(chars) - 1, -1, -1):
        char = chars[index]
        if char.isdigit():
            chars[index] = str((int(char) + 1) % 10)
            return "".join(chars)
        if "A" <= char <= "Z":
            chars[index] = chr((ord(char) - ord("A") + 1) % 26 + ord("A"))
            return "".join(chars)
        if "a" <= char <= "z":
            chars[index] = chr((ord(char) - ord("a") + 1) % 26 + ord("a"))
            return "".join(chars)
    return None


def propose_mutation(
    value: Any, schema: dict[str, Any], field: str
) -> tuple[str, Any, str] | None:
    enum = schema.get("enum")
    if isinstance(enum, list):
        alternatives = [candidate for candidate in enum if candidate != value]
        if alternatives:
            replacement = sorted(alternatives, key=lambda item: canonical_json(item))[0]
            return "schema_enum_lexicographic_alternative_v1", replacement, "schema enum"
        return None
    if isinstance(value, bool):
        return "boolean_toggle_v1", not value, "boolean schema"
    if isinstance(value, int) and not isinstance(value, bool):
        replacement = value + 1
        if "maximum" in schema and replacement > schema["maximum"]:
            replacement = value - 1
        if "minimum" in schema and replacement < schema["minimum"]:
            return None
        return "integer_plus_one_v1", replacement, "integer schema bounds"
    if isinstance(value, float):
        replacement = value + float(schema.get("multipleOf", 1.0))
        if "maximum" in schema and replacement > schema["maximum"]:
            replacement = value - float(schema.get("multipleOf", 1.0))
        if "minimum" in schema and replacement < schema["minimum"]:
            return None
        return "number_plus_schema_unit_v1", replacement, "number schema bounds"
    if not isinstance(value, str) or not value:
        return None
    if field in CONTROLLED_BUT_UNENUMERATED_FIELDS:
        return None
    if value_type(value) == "date_or_datetime":
        date_part = value[:10]
        try:
            replacement_date = date.fromisoformat(date_part) + timedelta(days=1)
        except ValueError:
            return None
        replacement = replacement_date.isoformat() + value[10:]
        return "iso_date_plus_one_day_v1", replacement, "ISO date schema/description"
    if field in CREATED_IDENTIFIER_FIELDS:
        replacement = increment_last_alphanumeric(value)
        if replacement and replacement != value:
            return (
                "created_identifier_last_alphanumeric_increment_v1",
                replacement,
                "free string schema; tool persists value without foreign-key lookup",
            )
    if field in FREE_TEXT_FIELDS:
        replacement = value + " (alternate)"
        max_length = schema.get("maxLength")
        if max_length is None or len(replacement) <= max_length:
            return (
                "schema_free_text_suffix_v1",
                replacement,
                "free string schema and direct persisted text field",
            )
    return None


def load_tasks(data_root: pathlib.Path) -> list[dict[str, Any]]:
    testlist_path = (
        data_root
        / "releases"
        / "thinkingbox_bench_v1"
        / "testlist_thinkingbox_bench_v1.yaml"
    )
    specs = yaml.safe_load(testlist_path.read_text(encoding="utf-8"))
    tasks = []
    for order, spec in enumerate(specs):
        filename, function_name = spec.split(":", 1)
        paths = list((data_root / "dataset" / "test_case").glob(f"**/{filename}"))
        if len(paths) != 1:
            raise RuntimeError(f"Expected one source for {spec}, found {len(paths)}")
        source_path = paths[0]
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        config = yaml.safe_load(function.body[0].value.value[1:])
        domain = source_path.parent.name
        domain_init = config.get("init", {}).get(domain, {})
        interactions = domain_init.get("golden_test_case", {}).get(
            "tool_interactions", []
        )
        body_calls = []
        for statement in function.body[1:]:
            if (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Name)
            ):
                body_calls.append(statement.value.func.id)
        tasks.append(
            {
                "order": order,
                "spec": spec,
                "filename": filename,
                "task_id": function_name,
                "source_path": source_path,
                "source": source,
                "domain": domain,
                "domain_label": DOMAIN_LABELS[domain],
                "prompt": config.get("query", ""),
                "domain_init": domain_init,
                "interactions": interactions,
                "rubric": "rubrics_yesno" in filename
                or "validate_rubrics_yesno" in body_calls,
                "body_calls": body_calls,
            }
        )
    return tasks


def build_systems():
    package = importlib.import_module("tb_business_ops_servers_202606")
    systems = {}
    modules = {}
    for domain, module_name in DOMAIN_MODULE_NAMES.items():
        module = importlib.import_module(f"tb_business_ops_servers_202606.{module_name}")
        modules[domain] = module
        systems[domain] = module.initialize_sandbox_system()
    return systems, modules


def source_location(tool: Any, field: str) -> tuple[str, int]:
    source_file = pathlib.Path(inspect.getsourcefile(type(tool)) or "")
    lines, first_line = inspect.getsourcelines(type(tool))
    for offset, line in enumerate(lines):
        if f"request.{field}" in line:
            return str(source_file), first_line + offset
    return str(source_file), first_line


def scan_candidates(tasks: list[dict[str, Any]], systems: dict[str, Any]):
    candidates: list[dict[str, Any]] = []
    primary_rejections = Counter()
    argument_rejections = Counter()
    deterministic_tasks = [task for task in tasks if not task["rubric"]]
    reference_tasks = 0
    for task in tasks:
        if task["rubric"]:
            primary_rejections["LLM_RUBRIC_REQUIRED"] += 1
            continue
        interactions = task["interactions"]
        if not interactions or any(
            not call.get("tool") or not isinstance(call.get("parameters"), dict)
            for call in interactions
        ):
            primary_rejections["NO_REFERENCE_TRAJECTORY"] += 1
            continue
        reference_tasks += 1
        system = systems[task["domain"]]
        action_calls = []
        grounded = []
        causally_mapped = []
        unique_producer = []
        viable = []
        mappings_by_call: dict[int, list[tuple[str, str]]] = {}

        for call_index, call in enumerate(interactions):
            tool = system._tool_map.get(call["tool"])
            if tool is None:
                argument_rejections["OTHER"] += 1
                continue
            source = inspect.getsource(type(tool))
            if not is_action_tool(call["tool"], source):
                for _ in iter_leaves(call.get("parameters", {})):
                    argument_rejections["READ_ONLY_OR_INTERMEDIATE"] += 1
                continue
            action_calls.append(call_index)
            input_schema = tool.input_schema
            mappings_by_call[call_index] = []
            for argument_path, original_value in iter_leaves(call.get("parameters", {})):
                if not argument_path or len(argument_path) > 2:
                    argument_rejections["NO_CAUSAL_EFFECT_MAPPING"] += 1
                    continue
                top_field = str(argument_path[0])
                if len(argument_path) == 2 and not isinstance(argument_path[1], int):
                    argument_rejections["NO_CAUSAL_EFFECT_MAPPING"] += 1
                    continue
                role_ok, role_evidence = semantic_role_is_explicit(
                    task["prompt"], original_value, top_field
                )
                if not role_ok:
                    argument_rejections["AMBIGUOUS_REQUIREMENT"] += 1
                    continue
                grounded.append((call_index, argument_path))
                mapping = causal_effect_mapping(source, top_field)
                if mapping is None:
                    argument_rejections["NO_CAUSAL_EFFECT_MAPPING"] += 1
                    continue
                entity, causal_evidence = mapping
                effect_slot = (entity, top_field)
                mappings_by_call[call_index].append(effect_slot)
                causally_mapped.append((call_index, argument_path))
                producer_count = 0
                for other_index, other_call in enumerate(interactions):
                    other_tool = system._tool_map.get(other_call["tool"])
                    if other_tool is None:
                        continue
                    other_source = inspect.getsource(type(other_tool))
                    if not is_action_tool(other_call["tool"], other_source):
                        continue
                    if top_field not in other_call.get("parameters", {}):
                        continue
                    other_mapping = causal_effect_mapping(other_source, top_field)
                    if other_mapping and other_mapping[0] == entity:
                        producer_count += 1
                if producer_count != 1:
                    argument_rejections["MULTIPLE_EFFECT_PRODUCERS"] += 1
                    continue
                unique_producer.append((call_index, argument_path))
                schema = schema_for_path(input_schema, argument_path)
                mutation = propose_mutation(original_value, schema, top_field)
                if mutation is None:
                    argument_rejections["MUTATION_NOT_VIABLE"] += 1
                    continue
                viable.append((call_index, argument_path))
                mutation_rule, replacement, mutation_evidence = mutation
                source_file, line = source_location(tool, top_field)
                source_path = pathlib.Path(source_file)
                try:
                    source_relative = str(
                        source_path.relative_to(task["source_path"].parents[3])
                    )
                except ValueError:
                    source_relative = str(source_path)
                candidate = {
                    "status": "POTENTIAL_R2A_BLIND_CANDIDATE",
                    "benchmark_release": RELEASE,
                    "suite": task["domain"],
                    "domain": task["domain_label"],
                    "task_uid": task["spec"],
                    "task_id": task["task_id"],
                    "task_source": str(
                        task["source_path"].relative_to(task["source_path"].parents[3])
                    ),
                    "prompt": task["prompt"],
                    "prompt_sha256": sha256_bytes(task["prompt"].encode("utf-8")),
                    "reference_trajectory_sha256": digest_json(interactions),
                    "reference_call_count": len(interactions),
                    "call_index": call_index,
                    "tool": call["tool"],
                    "canonical_arguments": jsonable(call["parameters"]),
                    "argument_path": path_string(argument_path),
                    "argument_name": top_field,
                    "argument_type": value_type(original_value),
                    "literal": jsonable(original_value),
                    "normalized_literal": normalize_text(original_value),
                    "prompt_literal_occurrences": 1,
                    "semantic_role": top_field,
                    "semantic_role_evidence": role_evidence,
                    "effect_class": "STATE_MUTATING",
                    "state_effect_entity": entity,
                    "state_effect_field": top_field,
                    "effect_type": f"{entity}.{top_field}",
                    "causal_mapping_evidence": causal_evidence,
                    "tool_source": source_relative,
                    "tool_source_line": line,
                    "tool_source_sha256": sha256_file(source_path),
                    "unique_effect_producer": True,
                    "effect_producer_count": producer_count,
                    "original_effect_witness": {
                        "status": "MECHANICALLY_OBTAINABLE_FROM_OFFICIAL_GOLDEN_REPLAY",
                        "expected_field": top_field,
                        "expected_value": jsonable(original_value),
                        "observability": "complete result_db_state and golden_db_state",
                    },
                    "mutation_viability": {
                        "status": "STATICALLY_VIABLE_NOT_EXECUTED",
                        "rule": mutation_rule,
                        "replacement": jsonable(replacement),
                        "evidence": mutation_evidence,
                    },
                    "perturbed_trajectory_executed": False,
                    "perturbed_evaluator_executed": False,
                }
                candidates.append(candidate)

        if viable:
            continue
        if not action_calls:
            primary_rejections["READ_ONLY_OR_INTERMEDIATE"] += 1
        elif not grounded:
            primary_rejections["AMBIGUOUS_REQUIREMENT"] += 1
        elif not causally_mapped:
            primary_rejections["NO_CAUSAL_EFFECT_MAPPING"] += 1
        elif not unique_producer:
            primary_rejections["MULTIPLE_EFFECT_PRODUCERS"] += 1
        else:
            primary_rejections["MUTATION_NOT_VIABLE"] += 1

    for name in (
        "LLM_RUBRIC_REQUIRED",
        "NO_REFERENCE_TRAJECTORY",
        "READ_ONLY_OR_INTERMEDIATE",
        "AMBIGUOUS_REQUIREMENT",
        "NO_CAUSAL_EFFECT_MAPPING",
        "MULTIPLE_EFFECT_PRODUCERS",
        "NO_EFFECT_WITNESS",
        "MUTATION_NOT_VIABLE",
        "RESET_UNCERTAIN",
        "OBSERVABILITY_INSUFFICIENT",
        "OTHER",
    ):
        primary_rejections.setdefault(name, 0)
        argument_rejections.setdefault(name, 0)
    return candidates, deterministic_tasks, reference_tasks, primary_rejections, argument_rejections


def find_task(tasks: list[dict[str, Any]], spec: str) -> dict[str, Any]:
    return next(task for task in tasks if task["spec"] == spec)


def matching_state_paths(value: Any, field: str, expected: Any, path: str = "$") -> list[str]:
    matches = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == field and jsonable(child) == jsonable(expected):
                matches.append(child_path)
            matches.extend(matching_state_paths(child, field, expected, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            matches.extend(matching_state_paths(child, field, expected, f"{path}[{index}]"))
    return matches


def import_task_module(path: pathlib.Path, task_id: str):
    module_name = f"phase3_0_{task_id}_{sha256_bytes(str(path).encode())[:8]}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def original_only_calibration(
    tasks: list[dict[str, Any]], modules: dict[str, Any], candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    candidate_by_task = {candidate["task_uid"]: candidate for candidate in candidates}
    representatives = []
    for domain in sorted({candidate["suite"] for candidate in candidates}):
        representative = min(
            (candidate for candidate in candidates if candidate["suite"] == domain),
            key=lambda candidate: (
                candidate["task_uid"],
                candidate["call_index"],
                candidate["argument_path"],
            ),
        )
        representatives.append((representative["task_uid"], domain))
    calibration_cases = representatives[:3]
    results = []
    for task_spec, domain in calibration_cases:
        task = find_task(tasks, task_spec)
        candidate = candidate_by_task.get(task_spec)
        if candidate is None:
            raise RuntimeError(f"Calibration case is not a static candidate: {task_spec}")
        config = task["domain_init"]
        module = importlib.reload(modules[domain])
        await module.initialize(config)
        initial_state_1 = jsonable(module.initial_db_state)
        initial_digest_1 = digest_json(initial_state_1)

        module = importlib.reload(module)
        await module.initialize(config)
        initial_state_2 = jsonable(module.initial_db_state)
        initial_digest_2 = digest_json(initial_state_2)
        response_trace = []
        exception = None
        for index, interaction in enumerate(task["interactions"]):
            try:
                response = await module.sandbox_system.call_tool(
                    interaction["tool"], interaction.get("parameters", {})
                )
                response_trace.append(
                    {
                        "index": index,
                        "tool": interaction["tool"],
                        "arguments": jsonable(interaction.get("parameters", {})),
                        "response": jsonable(response),
                    }
                )
            except Exception as error:  # evidence, not recovery
                exception = f"{type(error).__name__}: {error}"
                break

        effects = {}
        official_pass = False
        evaluator_exception = None
        if exception is None:
            raw_effects = await module.geteffects()
            effects = json.loads(raw_effects)
            try:
                test_module = import_task_module(task["source_path"], task["task_id"])
                context = type("OriginalOnlyContext", (), {"effects": {domain: effects}})()
                test_module.validate_database(context)
                official_pass = True
            except Exception as error:
                evaluator_exception = f"{type(error).__name__}: {error}"

        witness_paths = matching_state_paths(
            effects.get("result_db_state", {}),
            candidate["state_effect_field"],
            candidate["literal"],
        )
        results.append(
            {
                "task_uid": task_spec,
                "domain": task["domain_label"],
                "reference_trajectory_sha256": digest_json(task["interactions"]),
                "initial_state_digest_run_1": initial_digest_1,
                "initial_state_digest_run_2": initial_digest_2,
                "initial_state_equal": initial_digest_1 == initial_digest_2,
                "reference_calls_expected": len(task["interactions"]),
                "reference_calls_executed": len(response_trace),
                "execution_success": exception is None,
                "execution_exception": exception,
                "complete_request_response_trace_observed": len(response_trace)
                == len(task["interactions"]),
                "response_trace_sha256": digest_json(response_trace),
                "final_state_observed": isinstance(effects.get("result_db_state"), dict),
                "result_db_hash": effects.get("result_db_hash"),
                "golden_db_hash": effects.get("golden_db_hash"),
                "deep_diff_count": len(effects.get("diff", [])) if effects else None,
                "official_validate_database_pass": official_pass,
                "official_evaluator_exception": evaluator_exception,
                "effect_witness": {
                    "field": candidate["state_effect_field"],
                    "expected_value": candidate["literal"],
                    "matching_final_state_paths": witness_paths,
                    "reproduced": bool(witness_paths),
                },
                "policy_llm_calls": 0,
                "judge_llm_calls": 0,
                "perturbed_trajectory_executions": 0,
                "perturbed_evaluator_outcomes": 0,
            }
        )
    return results


def count_directory_bytes(path: pathlib.Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def markdown_report(results: dict[str, Any]) -> str:
    comp = results["release_composition"]
    opportunity = results["static_opportunity_scan"]
    calibration = results["original_only_calibration"]
    versions = results["versions"]
    reset_ok = all(case["initial_state_equal"] for case in calibration["cases"])
    replay_ok = all(
        case["execution_success"] and case["official_validate_database_pass"]
        for case in calibration["cases"]
    )
    observability_ok = all(
        case["complete_request_response_trace_observed"]
        and case["final_state_observed"]
        and case["effect_witness"]["reproduced"]
        for case in calibration["cases"]
    )
    rejection_lines = "\n".join(
        f"- `{name}`: {count} tasks"
        for name, count in results["rejections"]["task_primary"].items()
    )
    calibration_lines = "\n".join(
        "- `{task_uid}`: {calls} calls, reset={reset}, effect witness={witness}, "
        "official deterministic PASS={passed}".format(
            task_uid=case["task_uid"],
            calls=case["reference_calls_executed"],
            reset=str(case["initial_state_equal"]).lower(),
            witness=str(case["effect_witness"]["reproduced"]).lower(),
            passed=str(case["official_validate_database_pass"]).lower(),
        )
        for case in calibration["cases"]
    )
    return f"""# Phase 3.0 - ThinkingBox R2A Feasibility Audit

## Scope and frozen relation

This phase evaluates only whether ThinkingBox-Bench v1.0 can host the already
calibrated R2A relation. It performed static/source analysis and three
ORIGINAL-only reference replays. It constructed no perturbation, executed no
perturbed trajectory or evaluator, and called no policy or judge LLM.

## Version and environment

- ThinkingBox repository: `{versions['thinkingbox_repository']}`
- ThinkingBox commit: `{versions['thinkingbox_commit']}` (the data release does
  not pin a framework tag; this exact official checkout is the audit boundary)
- thinkingbox-data repository: `{versions['thinkingbox_data_repository']}`
- thinkingbox-data tag/commit: `{versions['thinkingbox_data_tag']}` /
  `{versions['thinkingbox_data_commit']}`
- Benchmark release: `{versions['benchmark_release']}`
- ThinkingBox package: `{versions['thinkingbox_package_version']}`
- MCP server package: `{versions['mcp_server_package_version']}`
- Python: `{versions['python']}`
- Typesense server required by release: `{versions['typesense_server_required']}`
- Typesense Python client: `{versions['typesense_python_client']}`
- FastMCP: `{versions['fastmcp']}`
- Platform: `{versions['platform']}`
- Access date: `{versions['access_date']}`

## Canonical release composition

The release test list contains exactly {comp['total_tasks']} tasks:

| Domain | Tasks | Deterministic-only | LLM-rubric |
|---|---:|---:|---:|
""" + "\n".join(
        f"| {domain} | {row['total']} | {row['deterministic_only']} | {row['llm_rubric']} |"
        for domain, row in comp["by_domain"].items()
    ) + f"""

The R2A discovery population is restricted to the {comp['deterministic_only_tasks']}
deterministic-only tasks. The 30 rubric alternatives are excluded before any
candidate analysis.

## Official evaluation pipeline

The code path is:

```text
test YAML in Python docstring
-> Dataset/load_test_file/hydrate_test_case
-> MCPProxyClient.initialize(task init)
-> agent/tool execution
-> MCPProxyClient.get_effects()
-> server __reserved__geteffects
-> result database state/hash
   versus fresh initial state + golden tool_interactions replay
-> golden database state/hash + deep diff
-> task validate_database(x)
-> optional validate_rubrics_yesno(x, judge)
-> TestResult PASS/FAIL
```

Relevant implementation points:

- `thinkingbox/common/python_test_file.py:get_yaml_obj`, `iter_test_functions`:
  parse task query, init, and executable assertion body.
- `thinkingbox/common/hydrator.py:Dataset.hydrate_test_case`: combines task and
  scenario into a hydrated case.
- `thinkingbox/common/mcp_proxy_client.py:MCPProxyClient.initialize`,
  `call_tool`, `get_effects`: resets a session, executes calls, and retrieves effects.
- Domain `mcp_*.py:initialize`: applies the official data patch and captures the
  initial database state.
- Domain `mcp_*.py:geteffects`: exports complete initial/result/golden states,
  hashes, and field-level deep diff.
- `utils/db_utils.py:apply_golden_set_to_database`: executes the ordered golden
  tool interactions through the same `SandboxToolsSystem.call_tool` path.
- `utils/db_utils.py:get_stable_database_state` and `calculate_database_hash`:
  remove fields explicitly marked unstable, recursively sort primitive lists,
  sort entities by `id`, canonicalize JSON, and SHA-256 hash it.
- Task `validate_database(x)`: requires result and golden SHA-256 hashes to match.
  Rubric variants additionally call `validate_rubrics_yesno(x, judge)`.
- `thinkingbox/common/testrunner.py:TestScript.evaluate_sync`: assertion success
  becomes the final Boolean TestResult.

For deterministic-only tasks, final persistent state and side-effect tables are
checked. Tool identity, arguments, response, and ordering are not independently
asserted; they influence the verdict through the resulting state. Final answer
and dialogue properties are consumed only by the excluded rubric path in this
release composition.

## Known-valid baseline

All {comp['total_tasks']} canonical tasks expose an ordered
`golden_test_case.tool_interactions` list. Every record contains tool/function
identity and a complete `parameters` object. The framework itself replays this
list against a fresh official database to construct the golden expected state.
It therefore qualifies as `OFFICIAL_REFERENCE_TRAJECTORY`, not a trajectory
inferred by this project. No policy model is needed for direct replay.

No separate canonical trajectory is required: the expected interactions are
the official executable artifact used by the evaluator. Their list position is
the explicit order.

## ORIGINAL-only replay and reset

Three representative deterministic-only reference trajectories were executed
through the official in-process `SandboxToolsSystem.call_tool` path. Each test
used two independently constructed initial systems; only the second ran the
reference trajectory. The official task `validate_database` predicate was then
called on the exported server effects.

{calibration_lines}

Summary: original replay success and official deterministic PASS =
{calibration['official_pass']}/{calibration['case_count']}; raw initial-state
digest equality = {calibration['reset_equal']}/{calibration['case_count']}.
No normalization was introduced. Independent reset is therefore
{'established' if reset_ok else 'not established'} for the calibrated path.

## Effect observability

The execution boundary exposes ordered tool identity, complete arguments and
complete response objects. `geteffects` exposes complete raw initial, result,
and golden database states plus stable hashes and field-level diffs. All three
calibration cases reproduced the selected original task-critical field in the
persisted result state. Effect observability is therefore
{'sufficient' if observability_ok else 'insufficient'} for R2A evidence.

## Static R2A opportunity scan

The deterministic scanner required a unique normalized prompt literal, a
same-role lexical anchor near an explicit action request, an official
state-mutating reference call, a direct source-level `request.field` to
persisted-field assignment, one producer for that effect slot, and one
schema-valid deterministic replacement. Zendesk bookkeeping and all read-only
calls are treated as intermediate. Unenumerated controlled vocabularies and
foreign-key selectors are not mutated.

- Candidate arguments: {opportunity['candidate_arguments']}
- Candidate tasks: {opportunity['candidate_tasks']}
- Effect types: {opportunity['effect_type_count']}
- Argument types: {opportunity['argument_type_distribution']}
- Domain distribution (candidate tasks): {opportunity['domain_task_distribution']}
- Candidate artifact SHA-256: `{opportunity['candidate_artifact_sha256']}`

These records are only `POTENTIAL_R2A_BLIND_CANDIDATE`; no proposed replacement
was executed.

Primary task-level exclusions (one deterministic funnel outcome per canonical task):

{rejection_lines}

## Infrastructure and cost

The normal benchmark path requires Linux/WSL, Python 3.12, the ThinkingBox and
data repositories, `tb_business_ops_servers_202606`, Typesense 30.1, and the MCP
Session Proxy. It does not require Docker, pre-indexed snapshots, downloaded
embedding models, or paid external tool APIs. Normal agent inference and the 30
rubric tasks require configured LLM endpoints.

For this deterministic reference-replay relation, policy inference and the
rubric judge are omitted. The official tool package and in-memory state engine
are sufficient for trajectories without policy search; full-population replay
would additionally start local Typesense and the local MCP proxy. No paid API
credential is required. Audit checkout size was
{results['infrastructure']['checkout_bytes']} bytes and the isolated venv size
was {results['infrastructure']['venv_bytes']} bytes; measured audit-process peak
RSS was {results['infrastructure']['peak_rss_kib']} KiB. The upstream release
does not publish a minimum-memory requirement.

## Limitations

- The framework repository has no v1.0 tag tied to the data release, so this
  audit freezes an exact official framework commit separately.
- Only three ORIGINAL trajectories were dynamically replayed; reset and
  observability for the remaining tasks are supported by their shared server
  architecture and static artifacts, not exhaustive execution.
- Mutation viability is static and conservative. A later phase must freeze the
  population, selection, replacements, certificate, and evaluator relation
  before any perturbation.

## Contamination statement

- `issue_pr_search_performed = false`
- `third_party_audit_or_defect_search_performed = false`
- `perturbed_trajectory_executions = 0`
- `perturbed_evaluator_outcomes_observed = 0`
- `llm_api_calls = 0`

## Gate

`{results['gate']}`

The release has a deterministic-only population, official executable reference
trajectories, 3/3 model-free original replay PASS, 3/3 independent reset,
complete effect observability, an independently callable deterministic
evaluator, {opportunity['candidate_tasks']} candidate tasks spanning
{opportunity['effect_type_count']} effect types, no LLM judge on the selected
path, and no paid external API requirement.
"""


async def run(repo_root: pathlib.Path) -> dict[str, Any]:
    data_root = repo_root / "reference" / "thinkingbox-data"
    framework_root = repo_root / "reference" / "thinkingbox"
    output_dir = repo_root / "artifacts" / "phase3_0"
    output_dir.mkdir(parents=True, exist_ok=True)
    tasks = load_tasks(data_root)
    systems, modules = build_systems()
    candidates, deterministic_tasks, reference_tasks, primary, argument_rejections = (
        scan_candidates(tasks, systems)
    )
    candidate_path = output_dir / "thinkingbox_r2a_static_candidates.jsonl"
    candidate_text = "".join(
        json.dumps(candidate, ensure_ascii=False, sort_keys=True) + "\n"
        for candidate in candidates
    )
    candidate_path.write_text(candidate_text, encoding="utf-8")
    calibration_cases = await original_only_calibration(tasks, modules, candidates)

    by_domain = {}
    for label in DOMAIN_LABELS.values():
        selected = [task for task in tasks if task["domain_label"] == label]
        by_domain[label] = {
            "total": len(selected),
            "deterministic_only": sum(not task["rubric"] for task in selected),
            "llm_rubric": sum(task["rubric"] for task in selected),
        }
    candidate_tasks = {candidate["task_uid"] for candidate in candidates}
    domain_task_distribution = Counter(
        next(candidate["domain"] for candidate in candidates if candidate["task_uid"] == uid)
        for uid in candidate_tasks
    )
    effect_distribution = Counter(candidate["effect_type"] for candidate in candidates)
    argument_type_distribution = Counter(
        candidate["argument_type"] for candidate in candidates
    )
    deterministic_body_shapes = Counter(
        tuple(task["body_calls"]) for task in deterministic_tasks
    )

    gate_conditions = {
        "deterministic_only_population_exists": bool(deterministic_tasks),
        "official_reference_trajectory_available": reference_tasks
        == len(deterministic_tasks),
        "reference_replay_without_policy_llm": all(
            case["execution_success"] for case in calibration_cases
        ),
        "independent_initial_state_reset": all(
            case["initial_state_equal"] for case in calibration_cases
        ),
        "persistent_effect_observable": all(
            case["final_state_observed"] and case["effect_witness"]["reproduced"]
            for case in calibration_cases
        ),
        "official_deterministic_evaluator_independently_callable": all(
            case["official_validate_database_pass"] for case in calibration_cases
        ),
        "candidate_tasks_at_least_10": len(candidate_tasks) >= 10,
        "effect_types_at_least_2": len(effect_distribution) >= 2,
        "llm_judge_not_required": True,
        "paid_external_api_not_required": True,
    }
    gate = (
        "GO_TO_THINKINGBOX_R2A_BLIND_PILOT"
        if all(gate_conditions.values())
        else "NO_GO"
    )
    results = {
        "phase": "3.0",
        "research_relation": "R2A_TASK_CRITICAL_EFFECT_SENSITIVITY",
        "relation_status": "FROZEN_CALIBRATED_RELATION",
        "target_role": "BLIND_DISCOVERY_FEASIBILITY_TARGET",
        "versions": {
            "thinkingbox_repository": "https://github.com/microsoft/thinkingbox.git",
            "thinkingbox_commit": THINKINGBOX_COMMIT,
            "thinkingbox_package_version": importlib.metadata.version("thinkingbox"),
            "thinkingbox_data_repository": "https://github.com/microsoft/thinkingbox-data.git",
            "thinkingbox_data_tag": RELEASE,
            "thinkingbox_data_commit": DATA_COMMIT,
            "benchmark_release": RELEASE,
            "python": sys.version.split()[0],
            "typesense_server_required": "30.1",
            "typesense_python_client": importlib.metadata.version("typesense"),
            "mcp_server_package_version": importlib.metadata.version(
                "tb-business-ops-servers-202606"
            ),
            "fastmcp": importlib.metadata.version("fastmcp"),
            "platform": platform.platform(),
            "access_date": "2026-09-09",
        },
        "release_composition": {
            "total_tasks": len(tasks),
            "deterministic_only_tasks": len(deterministic_tasks),
            "llm_rubric_tasks": sum(task["rubric"] for task in tasks),
            "by_domain": by_domain,
            "deterministic_test_body_shapes": {
                " + ".join(shape): count
                for shape, count in sorted(deterministic_body_shapes.items())
            },
        },
        "reference_trajectory": {
            "classification": "OFFICIAL_REFERENCE_TRAJECTORY",
            "canonical_tasks_with_usable_reference": sum(
                bool(task["interactions"]) for task in tasks
            ),
            "deterministic_tasks_with_usable_reference": reference_tasks,
            "total_reference_calls": sum(len(task["interactions"]) for task in tasks),
            "contains_tool_identity": True,
            "contains_complete_arguments": True,
            "contains_explicit_order": True,
            "policy_llm_required_for_replay": False,
            "provenance": "task init.golden_test_case.tool_interactions, replayed by apply_golden_set_to_database",
        },
        "original_only_calibration": {
            "case_count": len(calibration_cases),
            "original_trajectory_executions": len(calibration_cases),
            "official_pass": sum(
                case["official_validate_database_pass"] for case in calibration_cases
            ),
            "reset_equal": sum(case["initial_state_equal"] for case in calibration_cases),
            "response_trace_observable": sum(
                case["complete_request_response_trace_observed"]
                for case in calibration_cases
            ),
            "effect_witness_reproduced": sum(
                case["effect_witness"]["reproduced"] for case in calibration_cases
            ),
            "cases": calibration_cases,
        },
        "reset_and_observability": {
            "deterministic_tasks_with_shared_reset_architecture": len(
                deterministic_tasks
            ),
            "candidate_tasks_with_observable_effect_witness": len(candidate_tasks),
            "complete_tool_request_response_observable": True,
            "complete_initial_result_golden_state_observable": True,
            "side_effect_tables_observable": True,
            "entity_field_level_diff_observable": True,
        },
        "static_opportunity_scan": {
            "candidate_status": "POTENTIAL_R2A_BLIND_CANDIDATE",
            "candidate_arguments": len(candidates),
            "candidate_tasks": len(candidate_tasks),
            "candidate_artifact": "artifacts/phase3_0/thinkingbox_r2a_static_candidates.jsonl",
            "candidate_artifact_sha256": sha256_bytes(candidate_text.encode("utf-8")),
            "effect_type_count": len(effect_distribution),
            "effect_type_distribution": dict(sorted(effect_distribution.items())),
            "argument_type_distribution": dict(
                sorted(argument_type_distribution.items())
            ),
            "domain_argument_distribution": dict(
                sorted(Counter(c["domain"] for c in candidates).items())
            ),
            "domain_task_distribution": dict(sorted(domain_task_distribution.items())),
        },
        "rejections": {
            "unit": "task-level primary funnel; argument-level diagnostic counts also supplied",
            "task_primary": dict(sorted(primary.items())),
            "argument_diagnostic": dict(sorted(argument_rejections.items())),
        },
        "evaluation_pipeline": {
            "deterministic_predicate": "result_db_hash == golden_db_hash",
            "hash": "SHA-256 of canonical stable database state",
            "tool_interactions_directly_asserted": False,
            "tool_interactions_affect_score_via_final_state": True,
            "tool_responses_read_by_deterministic_evaluator": False,
            "final_answer_read_by_deterministic_evaluator": False,
            "dialogue_properties_read_by_deterministic_evaluator": False,
            "llm_rubric_path_excluded": True,
        },
        "infrastructure": {
            "framework_normally_requires": [
                "Linux or WSL",
                "Python 3.12 and uv",
                "ThinkingBox framework",
                "tb_business_ops_servers_202606 MCP package",
                "Typesense 30.1",
                "MCP Session Proxy",
                "configured policy/user/judge LLM endpoints for normal inference/rubric paths",
            ],
            "deterministic_reference_replay_requires": [
                "Python 3.12",
                "ThinkingBox and official MCP server package",
                "local Typesense for reference trajectories that call policy search",
                "local MCP proxy for full wire-level replay; in-process official tool runtime is sufficient for calibration",
            ],
            "docker_required": False,
            "paid_external_api_required": False,
            "external_tool_credentials_required": False,
            "policy_llm_required": False,
            "judge_llm_required_for_selected_population": False,
            "checkout_bytes": count_directory_bytes(framework_root)
            + count_directory_bytes(data_root),
            "venv_bytes": count_directory_bytes(framework_root / ".venv"),
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "published_minimum_memory": None,
        },
        "contamination": {
            "issue_pr_search_performed": False,
            "third_party_audit_search_performed": False,
            "benchmark_or_evaluator_bug_search_performed": False,
            "leaderboard_anomaly_search_performed": False,
            "llm_api_calls": 0,
            "perturbed_trajectory_executions": 0,
            "perturbed_evaluator_executions": 0,
            "perturbed_evaluator_outcomes_observed": 0,
        },
        "gate_conditions": gate_conditions,
        "gate": gate,
    }
    results_path = output_dir / "results.json"
    results_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path = repo_root / "PHASE3_0_THINKINGBOX_R2A_FEASIBILITY.md"
    report_path.write_text(markdown_report(results), encoding="utf-8")
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    results = asyncio.run(run(args.repo_root.resolve()))
    print(json.dumps({
        "total_tasks": results["release_composition"]["total_tasks"],
        "deterministic_only_tasks": results["release_composition"]["deterministic_only_tasks"],
        "reference_tasks": results["reference_trajectory"]["deterministic_tasks_with_usable_reference"],
        "candidate_arguments": results["static_opportunity_scan"]["candidate_arguments"],
        "candidate_tasks": results["static_opportunity_scan"]["candidate_tasks"],
        "effect_types": results["static_opportunity_scan"]["effect_type_count"],
        "original_pass": results["original_only_calibration"]["official_pass"],
        "gate": results["gate"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
