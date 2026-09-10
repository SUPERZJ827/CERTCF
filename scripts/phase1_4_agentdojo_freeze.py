#!/usr/bin/env python3
"""Verify and freeze the AgentDojo Phase 1.4 full eligible population."""

from __future__ import annotations

import importlib.metadata
import json
import platform
import shutil
import sys
from pathlib import Path

from phase1_3_agentdojo_common import BENCHMARK_VERSION, COMMIT, ROOT, canonical_bytes, file_digest, write_json
from phase1_3_agentdojo_freeze import load_eligible

SOURCE = ROOT / "artifacts" / "phase1_3_retry1"
PHASE12A = ROOT / "artifacts" / "phase1_2a"
OUT = ROOT / "artifacts" / "phase1_4"


def population_identity(record: dict) -> dict:
    return {
        "suite": record["suite"],
        "task_id": record["task_id"],
        "task_version": record["task_version"],
        "call_a_index": record["call_a_index"],
        "call_b_index": record["call_b_index"],
        "ground_truth_digest": record["ground_truth_digest"],
        "call_a_function": record["call_a"]["function"],
        "call_a_arguments": record["call_a"]["arguments"],
        "call_b_function": record["call_b"]["function"],
        "call_b_arguments": record["call_b"]["arguments"],
    }


def main() -> None:
    receipt_path = OUT / "FREEZE_RECEIPT.json"
    if receipt_path.exists():
        raise FileExistsError("Phase 1.4 receipt already exists and will not be overwritten")
    source_population = json.loads((SOURCE / "ELIGIBLE_CANDIDATES_V1.json").read_text())
    rebuilt_population = load_eligible()
    source_identity = sorted((population_identity(record) for record in source_population), key=canonical_bytes)
    rebuilt_identity = sorted((population_identity(record) for record in rebuilt_population), key=canonical_bytes)
    if source_identity != rebuilt_identity:
        raise RuntimeError("Phase 1.4 population differs from frozen Phase 1.2A eligibility")
    if len(source_population) != 184 or len({(r["suite"], r["task_id"]) for r in source_population}) != 26:
        raise RuntimeError("Phase 1.4 population is not 184 pairs / 26 tasks")

    write_json(OUT / "POPULATION_V1.json", source_population)
    write_json(OUT / "POPULATION_IDENTITY_CHECK.json", {"equal": True, "record_count": 184, "task_count": 26, "identity_fields": list(source_identity[0]), "records": source_identity})
    for name in ("TRANSFORMATION_SPECIFICATION_V1.txt", "VALIDITY_CERTIFICATE_SPECIFICATION_V1.txt", "EVALUATOR_RELATION_SPECIFICATION_V1.txt"):
        shutil.copyfile(SOURCE / name, OUT / name)

    runner = ROOT / "scripts" / "phase1_4_agentdojo_run.py"
    adapter = ROOT / "scripts" / "phase1_3_retry1_adapter.py"
    instrumentation = ROOT / "scripts" / "phase1_3_agentdojo_common.py"
    receipt = {
        "agentdojo_repository_url": "https://github.com/sequrity-ai/agentdojo",
        "agentdojo_commit": COMMIT,
        "agentdojo_package_version": "0.1.34",
        "benchmark_version": BENCHMARK_VERSION,
        "candidate_population_sha256": file_digest(OUT / "POPULATION_V1.json"),
        "population_identity_check_sha256": file_digest(OUT / "POPULATION_IDENTITY_CHECK.json"),
        "phase1_2a_eligibility_artifact_sha256": file_digest(PHASE12A / "candidate_impact.json"),
        "execution_runner_sha256": file_digest(runner),
        "adapter_sha256": file_digest(adapter),
        "instrumentation_sha256": file_digest(instrumentation),
        "transformation_specification_sha256": file_digest(OUT / "TRANSFORMATION_SPECIFICATION_V1.txt"),
        "validity_specification_sha256": file_digest(OUT / "VALIDITY_CERTIFICATE_SPECIFICATION_V1.txt"),
        "response_canonicalization_sha256": file_digest(ROOT / "artifacts/phase1_2/RESPONSE_CANONICALIZATION_V1.txt"),
        "state_canonicalization_sha256": file_digest(ROOT / "artifacts/phase1_2/STATE_CANONICALIZATION_V1.txt"),
        "evaluator_relation_specification_sha256": file_digest(OUT / "EVALUATOR_RELATION_SPECIFICATION_V1.txt"),
        "interpretation_specification_sha256": file_digest(OUT / "INTERPRETATION_SPECIFICATION_V1.txt"),
        "python_version": sys.version,
        "platform": platform.platform(),
        "dependency_versions": {name: importlib.metadata.version(name) for name in ("pydantic", "docstring-parser", "deepdiff", "pyyaml")},
        "population_selected_before_any_transformed_exposure": True,
        "population_selection_provenance": "Phase 1.2A eligible population frozen before Phase 1.3 transformed exposure",
        "population_pairs": 184,
        "population_tasks": 26,
        "transformation_changed_since_phase1_3": False,
        "eligibility_changed": False,
        "validity_criteria_changed": False,
        "canonicalization_changed": False,
        "evaluator_relation_changed": False,
        "issue_pr_search_performed": False,
        "llm_api_calls": 0,
    }
    write_json(receipt_path, receipt)


if __name__ == "__main__":
    main()
