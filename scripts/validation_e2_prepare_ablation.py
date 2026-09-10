#!/usr/bin/env python3
"""Freeze the shared E2 pool and an outcome-blinded adjudication index."""

from __future__ import annotations

import hashlib
import json
import platform
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "validation_e2"
SOURCES = [
    ("phase2_1", "AgentDojo", "transformed", 10),
    ("phase2_4", "AgentDojo", "perturbed", 9),
    ("phase3_2", "ThinkingBox", "perturbed", 30),
]


def load(path: Path):
    return json.loads(path.read_text())


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_file(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def canonical_digest(value) -> str:
    return digest_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


pool = []
for phase, benchmark, changed_branch, expected in SOURCES:
    case_root = ROOT / "artifacts" / phase / "cases"
    dirs = sorted(path for path in case_root.iterdir() if path.is_dir())
    if len(dirs) != expected:
        raise RuntimeError(f"{phase}: expected {expected} cases, found {len(dirs)}")
    for case_dir in dirs:
        metadata = load(case_dir / "metadata.json")
        unit = {
            "unit_id": canonical_digest([phase, case_dir.name]),
            "source_phase": phase,
            "benchmark": benchmark,
            "case_id": case_dir.name,
            "task_id": metadata.get("task_id"),
            "task_uid": metadata.get("task_uid"),
            "suite_or_domain": metadata.get("suite", metadata.get("domain")),
            "argument_type": metadata.get("argument_type"),
            "target_argument": metadata.get("target_argument", metadata.get("argument_name")),
            "target_call_index": metadata.get("target_call_index", metadata.get("call_index")),
            "original_branch": "original",
            "changed_branch": changed_branch,
            "evidence_root": str(case_dir.relative_to(ROOT)),
            "fault_schemas": [
                "DROP_REQUIRED_VALUE_CHECK",
                "WEAKEN_REQUIRED_VALUE_TO_EXISTENCE",
            ],
        }
        pool.append(unit)

pool.sort(key=lambda row: row["unit_id"])
OUT.mkdir(parents=True, exist_ok=True)
pool_path = OUT / "SHARED_CANDIDATE_POOL_V1.json"
pool_path.write_text(json.dumps(pool, indent=2, sort_keys=True) + "\n")

packet = []
for row in pool:
    root = Path(row["evidence_root"])
    packet.append(
        {
            "blind_order_key": canonical_digest(["E2_ADJUDICATION", row["unit_id"]]),
            "unit_id": row["unit_id"],
            "benchmark": row["benchmark"],
            "task_locator": {
                "task_id": row["task_id"],
                "task_uid": row["task_uid"],
                "suite_or_domain": row["suite_or_domain"],
            },
            "argument_locator": {
                "name": row["target_argument"],
                "call_index": row["target_call_index"],
                "type": row["argument_type"],
            },
            "allowed_evidence": {
                "original_trajectory": str(root / "original" / "trajectory.json"),
                "changed_trajectory": str(root / row["changed_branch"] / "trajectory.json"),
                "original_execution": str(root / "original" / "execution.json"),
                "changed_execution": str(root / row["changed_branch"] / "execution.json"),
                "original_final_state": str(root / "original" / "final_state.json"),
                "changed_final_state": str(root / row["changed_branch"] / "final_state.json"),
            },
            "excluded_from_adjudicator": [
                "all evaluator outcomes",
                "all gate labels",
                "all certificate classifications",
                "all mutant outcomes",
            ],
            "questions": [
                "Does the changed execution violate an explicit task-critical requirement?",
                "Is the changed argument causally responsible for a wrong persistent effect?",
                "Is the correct effect absent and uncompensated?",
                "Is the task interpretation unambiguous?",
            ],
        }
    )
packet.sort(key=lambda row: row["blind_order_key"])
packet_path = OUT / "BLINDED_ADJUDICATION_PACKET_V1.jsonl"
packet_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in packet))

protocol = ROOT / "CERTIFICATION_ABLATION_PROTOCOL.md"
receipt = {
    "phase": "E2_PREPARATION",
    "status": "POOL_FROZEN_PENDING_INDEPENDENT_ADJUDICATION",
    "candidate_pairs": len(pool),
    "candidate_tasks": len({(row["benchmark"], row["suite_or_domain"], row["task_id"]) for row in pool}),
    "source_counts": {phase: expected for phase, _, _, expected in SOURCES},
    "shared_pool_sha256": digest_file(pool_path),
    "blinded_packet_sha256": digest_file(packet_path),
    "protocol_sha256": digest_file(protocol),
    "e1_mutant_ledger_sha256": digest_file(ROOT / "artifacts" / "validation_e1" / "mutants.jsonl"),
    "candidate_pool_manual": False,
    "outcomes_in_adjudication_packet": False,
    "new_trajectory_executions": 0,
    "llm_api_calls": 0,
    "python_version": platform.python_version(),
    "platform": platform.platform(),
}
receipt_path = OUT / "FREEZE_RECEIPT.json"
if receipt_path.exists():
    raise RuntimeError("E2 freeze receipt already exists; refusing overwrite")
receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
print(json.dumps(receipt, indent=2, sort_keys=True))
