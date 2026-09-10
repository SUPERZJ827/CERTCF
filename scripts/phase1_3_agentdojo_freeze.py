#!/usr/bin/env python3
"""Create the Phase 1.3 task-diverse queues and true pre-execution receipt."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import sys
from pathlib import Path

from phase1_3_agentdojo_common import BENCHMARK_VERSION, COMMIT, OUT, REFERENCE, ROOT, digest, file_digest, write_json

PHASE12 = ROOT / "artifacts" / "phase1_2"
PHASE12A = ROOT / "artifacts" / "phase1_2a"


def load_eligible() -> list[dict]:
    impacts = json.loads((PHASE12A / "candidate_impact.json").read_text())
    eligible_ids = {item["candidate_id"] for item in impacts if item["decision"] == "ELIGIBLE_UNDER_SEMANTIC_REPEATABILITY"}
    records = []
    for line in (PHASE12 / "agentdojo_structured_candidates_v1.jsonl").read_text().splitlines():
        record = json.loads(line)
        if record.get("candidate_id") in eligible_ids:
            records.append(record)
    if len(records) != 184 or len({(r["suite"], r["task_id"]) for r in records}) != 26:
        raise RuntimeError("Frozen Phase 1.2A eligible population is not 184 pairs / 26 tasks")
    return records


def version_text(value: list[int]) -> str:
    return ".".join(str(part) for part in value)


def pair_selection_digest(record: dict) -> str:
    text = record["suite"] + record["task_id"] + version_text(record["task_version"]) + str(record["call_a_index"]) + str(record["call_b_index"]) + record["ground_truth_digest"]
    return hashlib.sha256(text.encode()).hexdigest()


def task_ranking_digest(record: dict) -> str:
    text = record["suite"] + record["task_id"] + version_text(record["task_version"]) + record["ground_truth_digest"]
    return hashlib.sha256(text.encode()).hexdigest()


def main() -> None:
    receipt_path = OUT / "FREEZE_RECEIPT.json"
    if receipt_path.exists():
        raise FileExistsError("Phase 1.3 receipt already exists and will not be overwritten")
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
        representative = min(records, key=lambda r: (r["representative_pair_digest"], r["candidate_id"]))
        representative = dict(representative)
        representative["task_ranking_digest"] = task_ranking_digest(representative)
        representatives.append(representative)
    representatives.sort(key=lambda r: (r["task_ranking_digest"], r["candidate_id"]))
    queue_fields = ("candidate_id", "suite", "task_id", "task_version", "call_a_index", "call_b_index", "ground_truth_digest", "representative_pair_digest", "task_ranking_digest", "call_a", "call_b", "semantic_order_checks")
    full_queue = [{key: record[key] for key in queue_fields} for record in representatives]
    pilot = full_queue[:10]
    write_json(OUT / "TASK_REPRESENTATIVES_V1.json", representatives)
    write_json(OUT / "PILOT_QUEUE_FULL_V1.json", full_queue)
    write_json(OUT / "PILOT_QUEUE_V1.json", pilot)

    runner = ROOT / "scripts" / "phase1_3_agentdojo_run.py"
    common = ROOT / "scripts" / "phase1_3_agentdojo_common.py"
    receipt = {
        "agentdojo_repository_url": "https://github.com/sequrity-ai/agentdojo",
        "agentdojo_commit": COMMIT,
        "agentdojo_package_version": "0.1.34",
        "benchmark_version": BENCHMARK_VERSION,
        "eligible_candidate_source_artifact": str(PHASE12A / "candidate_impact.json"),
        "eligible_candidate_source_sha256": file_digest(PHASE12A / "candidate_impact.json"),
        "eligible_candidate_population_sha256": file_digest(OUT / "ELIGIBLE_CANDIDATES_V1.json"),
        "phase1_2a_result_sha256": file_digest(PHASE12A / "results.json"),
        "candidate_selection_implementation_sha256": file_digest(Path(__file__)),
        "representative_population_sha256": file_digest(OUT / "TASK_REPRESENTATIVES_V1.json"),
        "full_queue_sha256": file_digest(OUT / "PILOT_QUEUE_FULL_V1.json"),
        "pilot_queue_sha256": file_digest(OUT / "PILOT_QUEUE_V1.json"),
        "execution_runner_sha256": file_digest(runner),
        "tracing_instrumentation_sha256": file_digest(common),
        "state_canonicalization_specification_sha256": file_digest(PHASE12 / "STATE_CANONICALIZATION_V1.txt"),
        "response_canonicalization_specification_sha256": file_digest(PHASE12 / "RESPONSE_CANONICALIZATION_V1.txt"),
        "transformation_specification_sha256": file_digest(OUT / "TRANSFORMATION_SPECIFICATION_V1.txt"),
        "validity_certificate_specification_sha256": file_digest(OUT / "VALIDITY_CERTIFICATE_SPECIFICATION_V1.txt"),
        "evaluator_relation_specification_sha256": file_digest(OUT / "EVALUATOR_RELATION_SPECIFICATION_V1.txt"),
        "python_version": sys.version,
        "platform": platform.platform(),
        "dependency_versions": {name: importlib.metadata.version(name) for name in ("pydantic", "docstring-parser", "deepdiff", "pyyaml")},
        "selection_encoding": "UTF-8 SHA256 of exact field concatenation; task_version encoded as dot-separated integers; candidate_id is deterministic tie-break only",
        "representative_count": len(representatives),
        "pilot_count": len(pilot),
        "candidate_population_manual": False,
        "pilot_selection_manual": False,
        "transformed_trajectory_executions_before_freeze": 0,
        "transformed_evaluator_outcomes_before_freeze": 0,
        "issue_pr_search_by_execution_agent": False,
        "transformation_changed_since_candidate_freeze": False,
        "eligibility_changed": False,
        "semantic_order_rules_changed": False,
        "validity_criteria_changed": False,
        "response_normalization_added": False,
        "evaluator_relation_changed": False,
    }
    write_json(receipt_path, receipt)


if __name__ == "__main__":
    main()
