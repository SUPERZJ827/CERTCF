#!/usr/bin/env python3
"""Recompute the identical Phase 1.3 queue and freeze retry1 before execution."""

from __future__ import annotations

import importlib.metadata
import json
import platform
import shutil
import sys
from pathlib import Path

from phase1_3_agentdojo_common import BENCHMARK_VERSION, COMMIT, ROOT, file_digest, write_json
from phase1_3_agentdojo_freeze import load_eligible, pair_selection_digest, task_ranking_digest

PARENT = ROOT / "artifacts" / "phase1_3"
OUT = ROOT / "artifacts" / "phase1_3_retry1"


def main() -> None:
    receipt_path = OUT / "FREEZE_RECEIPT.json"
    if receipt_path.exists():
        raise FileExistsError("Retry1 receipt already exists and will not be overwritten")

    eligible = load_eligible()
    eligible.sort(key=lambda r: (r["suite"], r["task_id"], r["call_a_index"], r["call_b_index"], r["candidate_id"]))
    write_json(OUT / "ELIGIBLE_CANDIDATES_V1.json", eligible)
    grouped: dict[tuple[str, str], list[dict]] = {}
    for record in eligible:
        grouped.setdefault((record["suite"], record["task_id"]), []).append(record)
    representatives = []
    for records in grouped.values():
        for record in records:
            record["representative_pair_digest"] = pair_selection_digest(record)
        representative = dict(min(records, key=lambda r: (r["representative_pair_digest"], r["candidate_id"])))
        representative["task_ranking_digest"] = task_ranking_digest(representative)
        representatives.append(representative)
    representatives.sort(key=lambda r: (r["task_ranking_digest"], r["candidate_id"]))
    queue_fields = ("candidate_id", "suite", "task_id", "task_version", "call_a_index", "call_b_index", "ground_truth_digest", "representative_pair_digest", "task_ranking_digest", "call_a", "call_b", "semantic_order_checks")
    full_queue = [{key: record[key] for key in queue_fields} for record in representatives]
    pilot = full_queue[:10]
    write_json(OUT / "TASK_REPRESENTATIVES_V1.json", representatives)
    write_json(OUT / "PILOT_QUEUE_FULL_V1.json", full_queue)
    write_json(OUT / "PILOT_QUEUE_V1.json", pilot)

    parent_pilot = json.loads((PARENT / "PILOT_QUEUE_V1.json").read_text())
    identity_fields = ("suite", "task_id", "task_version", "call_a_index", "call_b_index", "ground_truth_digest")
    retry_identity = [{key: row[key] for key in identity_fields} for row in pilot]
    parent_identity = [{key: row[key] for key in identity_fields} for row in parent_pilot]
    if retry_identity != parent_identity:
        raise RuntimeError("Retry1 pilot queue differs from original Phase 1.3 queue")
    write_json(OUT / "QUEUE_IDENTITY_CHECK.json", {"equal": True, "fields": list(identity_fields), "ordered_items": retry_identity})

    for name in ("TRANSFORMATION_SPECIFICATION_V1.txt", "VALIDITY_CERTIFICATE_SPECIFICATION_V1.txt", "EVALUATOR_RELATION_SPECIFICATION_V1.txt"):
        shutil.copyfile(PARENT / name, OUT / name)

    runner = ROOT / "scripts" / "phase1_3_retry1_agentdojo_run.py"
    adapter = ROOT / "scripts" / "phase1_3_retry1_adapter.py"
    common = ROOT / "scripts" / "phase1_3_agentdojo_common.py"
    receipt = {
        "parent_attempt": "phase1_3",
        "parent_attempt_status": "ABORTED_PRE_TRANSFORMED_ADAPTER_ERROR",
        "retry_index": 1,
        "retry_reason": "PRE_TRANSFORMED_ADAPTER_FIELD_MISMATCH",
        "prior_original_trajectory_executions": 1,
        "prior_original_utility_outcomes": 0,
        "prior_transformed_trajectory_executions": 0,
        "prior_transformed_evaluator_executions": 0,
        "prior_transformed_evaluator_outcomes": 0,
        "prior_llm_api_calls": 0,
        "adapter_fix": "arguments_to_args",
        "agentdojo_repository_url": "https://github.com/sequrity-ai/agentdojo",
        "agentdojo_commit": COMMIT,
        "agentdojo_package_version": "0.1.34",
        "benchmark_version": BENCHMARK_VERSION,
        "repaired_runner_sha256": file_digest(runner),
        "adapter_sha256": file_digest(adapter),
        "adapter_fix_audit_sha256": file_digest(OUT / "ADAPTER_FIX_AUDIT.json"),
        "eligible_candidate_artifact_sha256": file_digest(OUT / "ELIGIBLE_CANDIDATES_V1.json"),
        "representative_artifact_sha256": file_digest(OUT / "TASK_REPRESENTATIVES_V1.json"),
        "full_queue_sha256": file_digest(OUT / "PILOT_QUEUE_FULL_V1.json"),
        "pilot_queue_sha256": file_digest(OUT / "PILOT_QUEUE_V1.json"),
        "queue_identity_check_sha256": file_digest(OUT / "QUEUE_IDENTITY_CHECK.json"),
        "transformation_specification_sha256": file_digest(OUT / "TRANSFORMATION_SPECIFICATION_V1.txt"),
        "validity_specification_sha256": file_digest(OUT / "VALIDITY_CERTIFICATE_SPECIFICATION_V1.txt"),
        "evaluator_relation_specification_sha256": file_digest(OUT / "EVALUATOR_RELATION_SPECIFICATION_V1.txt"),
        "instrumentation_sha256": file_digest(common),
        "state_canonicalization_specification_sha256": file_digest(ROOT / "artifacts/phase1_2/STATE_CANONICALIZATION_V1.txt"),
        "response_canonicalization_specification_sha256": file_digest(ROOT / "artifacts/phase1_2/RESPONSE_CANONICALIZATION_V1.txt"),
        "python_version": sys.version,
        "platform": platform.platform(),
        "dependency_versions": {name: importlib.metadata.version(name) for name in ("pydantic", "docstring-parser", "deepdiff", "pyyaml")},
        "candidate_population_changed": False,
        "pilot_queue_changed": False,
        "selection_algorithm_changed": False,
        "transformation_changed": False,
        "eligibility_changed": False,
        "semantic_order_rules_changed": False,
        "validity_criteria_changed": False,
        "canonicalization_changed": False,
        "evaluator_relation_changed": False,
        "transformed_trajectory_executions_before_retry1_freeze": 0,
        "transformed_evaluator_outcomes_before_retry1_freeze": 0,
        "issue_pr_search_by_execution_agent": False,
        "candidate_selection_manual": False,
        "pilot_selection_manual": False,
    }
    write_json(receipt_path, receipt)


if __name__ == "__main__":
    main()
