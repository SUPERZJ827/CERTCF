#!/usr/bin/env python3
"""Diagnose E1 survivors without changing the frozen E1 experiment."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "artifacts" / "validation_e1" / "mutants.jsonl"
OUT = ROOT / "artifacts" / "validation_e1_diagnosis"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def leaf(path: str) -> str:
    return path.rsplit(".", 1)[-1]


rows = [json.loads(line) for line in SOURCE.read_text().splitlines() if line]
survivors = [row for row in rows if row["status"] == "SURVIVED"]
audits = []
for row in survivors:
    diffs = row["evidence"]["official_diff"]
    target = row["evidence"]["target_field"]
    target_diffs = [item for item in diffs if leaf(item["path"]) == target]
    collateral_diffs = [item for item in diffs if leaf(item["path"]) != target]
    audits.append(
        {
            "mutant_id": row["mutant_id"],
            "case_id": row["evidence"]["case_id"],
            "task_id": row["task_id"],
            "fault_class": row["fault_class"],
            "target_field": target,
            "official_hash_input_scope": "complete final database state",
            "target_mismatch_count": len(target_diffs),
            "target_mismatches": target_diffs,
            "collateral_mismatch_count": len(collateral_diffs),
            "collateral_mismatches": collateral_diffs,
            "local_mutant_effect": "target mismatch removed from semantic projection",
            "local_check_changed": bool(target_diffs),
            "final_mutant_verdict": "FAIL",
            "diagnosis": (
                "MASKED_BY_PROPAGATED_DERIVED_STATE_DIFFERENCE"
                if target_diffs and collateral_diffs
                else "CAUSE_UNRESOLVED"
            ),
            "legal_domain_equivalence": "UNRESOLVED",
            "reason_equivalence_unresolved": (
                "Persisted evidence proves correlated differences but not whether every "
                "legal state changing the target necessarily changes them."
            ),
        }
    )

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "survivor_audit.jsonl").write_text(
    "".join(json.dumps(row, sort_keys=True) + "\n" for row in audits)
)

status = Counter(row["status"] for row in rows)
by_fault = {}
for fault in sorted({row["fault_class"] for row in rows}):
    subset = [row for row in rows if row["fault_class"] == fault]
    counts = Counter(row["status"] for row in subset)
    by_fault[fault] = {"total": len(subset), **dict(sorted(counts.items()))}

collateral_fields = Counter(
    leaf(item["path"])
    for row in audits
    for item in row["collateral_mismatches"]
)
results = {
    "phase": "E1-D",
    "source_mutant_ledger_sha256": sha256(SOURCE),
    "source_results_preserved": True,
    "operation_definitions": {
        "valid": "mutant preserves an official original PASS baseline",
        "activated": (
            "valid mutant with persisted certified counterfactual evidence that reaches "
            "the mutated semantic atom"
        ),
        "killed": (
            "activated mutant returns a verdict that violates the frozen evaluator relation; "
            "mutant crashes and baseline rejection do not count"
        ),
        "survived": "activated mutant retains the relation-expected final verdict",
        "uncovered": "valid mutant for which no certified case reaches the mutated atom",
        "invalid": "mutant does not preserve an original PASS baseline",
    },
    "accounting": {
        "total": len(rows),
        "valid": len(rows) - status["INVALID_MUTANT"],
        "activated": status["KILLED"] + status["SURVIVED"],
        "killed": status["KILLED"],
        "survived": status["SURVIVED"],
        "uncovered": status["UNCOVERED"],
        "invalid": status["INVALID_MUTANT"],
        "killed_over_activated": status["KILLED"] / (status["KILLED"] + status["SURVIVED"]),
        "activated_over_valid": (status["KILLED"] + status["SURVIVED"]) / (len(rows) - status["INVALID_MUTANT"]),
        "killed_over_valid": status["KILLED"] / (len(rows) - status["INVALID_MUTANT"]),
    },
    "missing_summary_group": {
        "fault_class": "DROP_REQUIRED_CONJUNCT",
        "relation": "R3",
        "activated": 11,
        "killed": 11,
        "description": "one omitted-effect conjunct mutant per certified R3 case",
    },
    "by_fault_class": by_fault,
    "survivor_diagnosis": {
        "mutant_rows": len(audits),
        "unique_counterfactuals": len({(row["case_id"], row["target_field"]) for row in audits}),
        "tasks": len({row["task_id"] for row in audits}),
        "all_target_checks_locally_changed": all(row["local_check_changed"] for row in audits),
        "all_have_collateral_differences": all(row["collateral_mismatch_count"] > 0 for row in audits),
        "diagnosis_counts": dict(Counter(row["diagnosis"] for row in audits)),
        "collateral_field_counts": dict(sorted(collateral_fields.items())),
        "equivalent_mutants_proven": 0,
        "equivalence_unresolved": len(audits),
    },
    "new_fault_model_created": False,
    "new_trajectory_executions": 0,
    "llm_api_calls": 0,
}
(OUT / "results.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
print(json.dumps(results, indent=2, sort_keys=True))
