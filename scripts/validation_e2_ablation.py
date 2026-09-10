#!/usr/bin/env python3
"""Compute the mechanical portion of E2; never infer missing semantic labels."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "validation_e2"


def load(path: Path):
    return json.loads(path.read_text())


def official_pass(payload) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("official_verdict") is True or payload.get("verdict") == "PASS":
        return True
    if payload.get("baseline_success") is True and payload.get("sensitivity_classification"):
        return True
    result = payload.get("result")
    if payload.get("status") == "COMPLETED" and isinstance(result, dict):
        return result.get("success") is True
    return False


def branch_pass(case_dir: Path, branch: str, metadata: dict) -> bool:
    path = case_dir / branch / "evaluator.json"
    if path.exists() and official_pass(load(path)):
        return True
    if branch == "original":
        return metadata.get("baseline_success") is True
    return metadata.get("sensitivity_classification") in {
        "R2A_FALSE_ACCEPTANCE_CANDIDATE",
        "EVALUATOR_FALSE_ACCEPTANCE_CANDIDATE",
    }


audit_labels = {}
for path in sorted((ROOT / "artifacts" / "phase2_2" / "cases").glob("*/semantic_chain.json")):
    audit_labels[path.parent.name] = load(path)["final_classification"]

pool = load(OUT / "SHARED_CANDIDATE_POOL_V1.json")
units = []
for row in pool:
    case_dir = ROOT / row["evidence_root"]
    metadata = load(case_dir / "metadata.json")
    phase = row["source_phase"]
    changed = row["changed_branch"]
    original_ok = branch_pass(case_dir, "original", metadata)
    changed_ok = False
    simple_effect = False
    full = False
    if phase == "phase2_1":
        exact = load(case_dir / "mutation_exactness_certificate.json")
        cert = load(case_dir / "semantic_violation_certificate.json")
        g0 = exact.get("status") == "PASS"
        changed_ok = bool(cert.get("checks", {}).get("perturbed_target_execution_success"))
        simple_effect = any(cert.get("observable_behavior", {}).values())
        full = False
        full_reason = "old R2 certificate is not a task-critical effect certificate"
    elif phase == "phase2_4":
        exact = load(case_dir / "mutation_exactness_certificate.json")
        cert = load(case_dir / "task_critical_violation_certificate.json")
        g0 = exact.get("status") == "PASS"
        changed_ok = bool(cert.get("checks", {}).get("perturbed_target_action_success"))
        simple_effect = cert.get("wrong_effect_witness") is not None
        full = cert.get("classification") == "TASK_CRITICAL_VIOLATION_CERTIFIED"
        full_reason = cert.get("reason")
    else:
        exact = load(case_dir / "mutation_exactness_certificate.json")
        cert = load(case_dir / "task_critical_effect_certificate.json")
        g0 = exact.get("status") == "MUTATION_EXACTNESS_SUCCESS"
        changed_ok = bool(cert.get("checks", {}).get("perturbed_success"))
        simple_effect = bool(cert.get("perturbed_witness")) or (
            load(case_dir / "original" / "final_state.json")
            != load(case_dir / changed / "final_state.json")
        )
        full = cert.get("classification") == "TASK_CRITICAL_VIOLATION_CERTIFIED"
        full_reason = cert.get("reason")
    gates = {
        "G0_LITERAL_ONLY": bool(g0),
        "G1_EXECUTION_SUCCESS": bool(g0 and original_ok and changed_ok),
        "G2_SIMPLE_EFFECT": bool(g0 and original_ok and changed_ok and simple_effect),
        "G3_FULL_TASK_EFFECT_CERTIFICATE": bool(g0 and original_ok and changed_ok and simple_effect and full),
    }
    claim = original_ok and branch_pass(case_dir, changed, metadata)
    units.append(
        {
            **row,
            "gates": gates,
            "official_pass_claim": claim,
            "independent_claim_audit": audit_labels.get(row["case_id"]),
            "full_gate_exclusion_reason": full_reason,
        }
    )

gate_results = {}
for gate in ["G0_LITERAL_ONLY", "G1_EXECUTION_SUCCESS", "G2_SIMPLE_EFFECT", "G3_FULL_TASK_EFFECT_CERTIFICATE"]:
    admitted = [row for row in units if row["gates"][gate]]
    claims = [row for row in admitted if row["official_pass_claim"]]
    labels = Counter(row["independent_claim_audit"] or "ADJUDICATION_REQUIRED" for row in claims)
    gate_results[gate] = {
        "retained_pairs": len(admitted),
        "retained_tasks": len({(row["benchmark"], row["suite_or_domain"], row["task_id"]) for row in admitted}),
        "official_pass_defect_claims": len(claims),
        "claim_audit_labels": dict(sorted(labels.items())),
        "confirmed_defect_claims": labels["CONFIRMED_EVALUATOR_FALSE_ACCEPTANCE"],
        "unsupported_or_ambiguous_claims": len(claims) - labels["CONFIRMED_EVALUATOR_FALSE_ACCEPTANCE"],
    }

# The currently frozen source adapter covers the 30 ThinkingBox cases only.
e1_rows = [
    json.loads(line)
    for line in (ROOT / "artifacts" / "validation_e1" / "mutants.jsonl").read_text().splitlines()
    if line
]
e1_rows = [row for row in e1_rows if row["relation_evidence"] == "R2A"]
tb_by_case = {row["case_id"]: row for row in units if row["source_phase"] == "phase3_2"}
detection = {}
for gate in gate_results:
    counts = Counter()
    for mutant in e1_rows:
        unit = tb_by_case[mutant["evidence"]["case_id"]]
        counts["fixed_operational_mutants"] += 1
        if not unit["gates"][gate]:
            counts["uncovered"] += 1
            continue
        target = mutant["evidence"]["target_field"]
        diffs = mutant["evidence"]["official_diff"]
        target_diffs = [item for item in diffs if item["path"].rsplit(".", 1)[-1] == target]
        if not target_diffs:
            counts["uncovered"] += 1
            continue
        counts["reached"] += 1
        collateral = [item for item in diffs if item not in target_diffs]
        if collateral:
            counts["survived"] += 1
        else:
            counts["killed"] += 1
    valid = counts["fixed_operational_mutants"]
    reached = counts["reached"]
    detection[gate] = {
        **dict(counts),
        "killed_over_fixed_operational": counts["killed"] / valid if valid else None,
        "killed_over_reached": counts["killed"] / reached if reached else None,
    }

adjudicated_units = {row["unit_id"] for row in units if row["independent_claim_audit"]}
results = {
    "phase": "E2_PRELIMINARY",
    "status": "ADJUDICATION_REQUIRED_FOR_COMPLETE_ABLATION",
    "shared_candidate_pairs": len(units),
    "shared_candidate_tasks": len({(row["benchmark"], row["suite_or_domain"], row["task_id"]) for row in units}),
    "gate_results": gate_results,
    "detection_control": {
        "intended_fault_instances": len(units) * 2,
        "operational_source_adapter_instances": len(e1_rows),
        "adapter_unavailable_instances": len(units) * 2 - len(e1_rows),
        "scope": "ThinkingBox source adapter only",
        "by_gate": detection,
        "equivalent_or_masked_mutants_resolved": False,
    },
    "independent_semantic_adjudication": {
        "units_labeled": len(adjudicated_units),
        "units_unlabeled": len(units) - len(adjudicated_units),
        "existing_labels_apply_to_all_official_pass_claims": all(
            row["independent_claim_audit"] is not None for row in units if row["official_pass_claim"]
        ),
        "semantic_precision_of_all_retained_tests_estimable": False,
        "human_time_cost": "not recorded in persisted Phase 2.2 artifacts",
    },
    "new_trajectory_executions": 0,
    "llm_api_calls": 0,
}
(OUT / "gate_assignments.jsonl").write_text(
    "".join(json.dumps(row, sort_keys=True) + "\n" for row in units)
)
(OUT / "results.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
print(json.dumps(results, indent=2, sort_keys=True))
