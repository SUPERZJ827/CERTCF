#!/usr/bin/env python3
"""Recompute selected paper counts from the compact public ledgers.

This is a ledger check, not a benchmark rerun.  The expected snapshot is
kept separately so a changed ledger cannot silently change the public claim.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LEDGER_DIR = ROOT / "artifacts" / "quick" / "final_ledgers"
LEDGER = LEDGER_DIR / "results_ledger.json"
R2_SCAN = LEDGER_DIR / "phase2_0_results.json"
E2_FINAL = LEDGER_DIR / "validation_e2_final_results.json"
E3_FINAL = LEDGER_DIR / "validation_e3_final_results.json"
EXPECTED = ROOT / "artifacts" / "quick" / "expected_outputs" / "headline_counts.json"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def derive(ledger: dict[str, Any]) -> dict[str, Any]:
    r1 = ledger["relations"]["R1"]
    r2 = ledger["relations"]["R2_REJECTED"]
    r2a = ledger["relations"]["R2A"]
    r3 = ledger["relations"]["R3_R3V2"]
    r4 = ledger["relations"]["R4"]
    return {
        "r1": {
            "appworld_exact_pairs": r1["appworld"]["candidate_population_pairs"],
            "appworld_attempted": r1["appworld"]["attempted"],
            "appworld_certified_invariant": r1["appworld"]["certified"],
            "agentdojo_population_pairs": r1["agentdojo"]["population_pairs"],
            "agentdojo_certified_invariant": r1["agentdojo"]["certified_pairs"],
            "agentdojo_covered_tasks": r1["agentdojo"]["covered_tasks"],
            "agentdojo_baseline_replay_unknown": r1["agentdojo"]["exclusions"]["baseline_replay_failed"],
            "agentdojo_failed_relation_instances": r1["agentdojo"]["exclusions"]["final_state_mismatch"],
        },
        "r2": {
            "arguments_scanned": r2["phase2_0_candidates"]["arguments"],
            "literal_candidates": r2["phase2_0_candidates"]["arguments"],
            "candidate_tasks": r2["phase2_0_candidates"]["tasks"],
            "pilot_tasks": r2["phase2_1"]["attempted"],
            "apparent_false_acceptances": r2["phase2_1"]["apparent_false_acceptances"],
            "confirmed_task_effect_defects": r2["phase2_2_audit"]["confirmed"],
        },
        "r2a": {
            "agentdojo_certified": r2a["agentdojo_heldout"]["certified"],
            "agentdojo_sensitive": r2a["agentdojo_heldout"]["sensitive"],
            "thinkingbox_candidates": r2a["thinkingbox_full"]["candidates"],
            "thinkingbox_certified": r2a["thinkingbox_full"]["certified"],
            "thinkingbox_sensitive": r2a["thinkingbox_full"]["sensitive"],
            "primary_certified": r2a["combined_certified"],
        },
        "r3": {
            "thinkingbox_certified_sensitive": r3["thinkingbox_v1"]["certified"],
            "appworld_certified_sensitive": r3["appworld"]["certified"],
            "total_certified_sensitive": r3["thinkingbox_v1"]["sensitive"] + r3["appworld"]["sensitive"],
            "thinkingbox_heldout_tasks": r3["thinkingbox_heldout"]["previously_unexposed_tasks"],
            "thinkingbox_strict_candidate_tasks": r3["thinkingbox_heldout"]["strict_candidate_tasks"],
        },
        "r4": {
            "usable_tasks": r4["usable_tasks_scanned"],
            "strict_static_candidates": r4["thinkingbox_candidates"] + r4["appworld_candidates"] + r4["agentdojo_candidates"],
            "executed": r4["perturbation_executions"],
        },
    }


def main() -> int:
    actual = derive(load(LEDGER))
    r2_scan = load(R2_SCAN)
    actual["r2"]["arguments_scanned"] = r2_scan["total_gt_arguments"]
    actual["r2"]["literal_candidates"] = r2_scan["candidate_arguments"]
    actual["r2"]["candidate_tasks"] = r2_scan["candidate_tasks"]
    e2 = load(E2_FINAL)
    e3 = load(E3_FINAL)
    actual["e2"] = {
        "historical_pairs": e2["coverage"]["historical_pairs"],
        "violations": e2["semantic_labels"]["violations"],
        "satisfied": e2["semantic_labels"]["satisfied"],
        "ambiguous": e2["semantic_labels"]["ambiguous"],
        "insufficient": e2["semantic_labels"]["insufficient"],
        "full_gate_retained": e2["full_gate"]["retained_total"],
        "full_gate_determinate_validity": e2["full_gate"]["determinate_retained_validity"],
        "full_gate_retained_labeled_violations": e2["full_gate"]["retained_labeled_violation_fraction"],
    }
    actual["e3"] = {
        "appworld_cases": e3["coverage"]["appworld_cases"],
        "semantic_violations": e3["semantic_labels"]["violations"],
        "certified": e3["certificate_and_evaluator_relation"]["certified"],
        "sensitive": e3["certificate_and_evaluator_relation"]["sensitive"],
    }
    expected = load(EXPECTED)
    if actual != expected:
        print("PUBLIC LEDGER CHECK: FAILED")
        print("Derived:")
        print(json.dumps(actual, indent=2, sort_keys=True))
        print("Expected:")
        print(json.dumps(expected, indent=2, sort_keys=True))
        return 1
    print("PUBLIC LEDGER CHECK: PASS")
    print(json.dumps(actual, indent=2, sort_keys=True))
    print("Source: artifacts/quick/final_ledgers/results_ledger.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
