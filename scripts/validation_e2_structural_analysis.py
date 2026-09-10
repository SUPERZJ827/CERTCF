#!/usr/bin/env python3
"""Cross-tab E2 gates, compare mutant identities, and audit adapter feasibility."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
E2 = ROOT / "artifacts" / "validation_e2"
OUT = ROOT / "artifacts" / "validation_e2_analysis"
GATES = [
    "G0_LITERAL_ONLY",
    "G1_EXECUTION_SUCCESS",
    "G2_SIMPLE_EFFECT",
    "G3_FULL_TASK_EFFECT_CERTIFICATE",
]


def load(path: Path):
    return json.loads(path.read_text())


units = [json.loads(line) for line in (E2 / "gate_assignments.jsonl").read_text().splitlines() if line]
mutants = [
    json.loads(line)
    for line in (ROOT / "artifacts" / "validation_e1" / "mutants.jsonl").read_text().splitlines()
    if line
]
mutants = [row for row in mutants if row["relation_evidence"] == "R2A"]

cross_tabs = {}
for phase in sorted({row["source_phase"] for row in units}):
    subset = [row for row in units if row["source_phase"] == phase]
    cross_tabs[phase] = {
        "pool_pairs": len(subset),
        "pool_tasks": len({(row["suite_or_domain"], row["task_id"]) for row in subset}),
        "by_gate": {},
    }
    for gate in GATES:
        admitted = [row for row in subset if row["gates"][gate]]
        claims = [row for row in admitted if row["official_pass_claim"]]
        cross_tabs[phase]["by_gate"][gate] = {
            "retained_pairs": len(admitted),
            "retained_tasks": len({(row["suite_or_domain"], row["task_id"]) for row in admitted}),
            "claims": len(claims),
            "claim_case_ids": sorted(row["case_id"] for row in claims),
        }

unit_by_tb_case = {row["case_id"]: row for row in units if row["source_phase"] == "phase3_2"}
sets = {}
for gate in GATES:
    retained_cases = {row["case_id"] for row in units if row["gates"][gate]}
    reached = set()
    killed = set()
    reached_cases = set()
    killed_cases = set()
    for mutant in mutants:
        case_id = mutant["evidence"]["case_id"]
        unit = unit_by_tb_case[case_id]
        if case_id not in retained_cases:
            continue
        target = mutant["evidence"]["target_field"]
        diffs = mutant["evidence"]["official_diff"]
        target_diffs = [item for item in diffs if item["path"].rsplit(".", 1)[-1] == target]
        if not target_diffs:
            continue
        reached.add(mutant["mutant_id"])
        reached_cases.add(case_id)
        collateral = [item for item in diffs if item not in target_diffs]
        if not collateral:
            killed.add(mutant["mutant_id"])
            killed_cases.add(case_id)
    sets[gate] = {
        "retained_pair_ids": sorted(row["unit_id"] for row in units if row["gates"][gate]),
        "retained_thinkingbox_case_ids": sorted(retained_cases & set(unit_by_tb_case)),
        "reached_mutant_ids": sorted(reached),
        "killed_mutant_ids": sorted(killed),
        "reached_case_ids": sorted(reached_cases),
        "killed_case_ids": sorted(killed_cases),
    }

baseline = sets["G0_LITERAL_ONLY"]
set_comparison = {}
for gate, values in sets.items():
    reached = set(values["reached_mutant_ids"])
    killed = set(values["killed_mutant_ids"])
    set_comparison[gate] = {
        "reached_set_equals_G0": reached == set(baseline["reached_mutant_ids"]),
        "killed_set_equals_G0": killed == set(baseline["killed_mutant_ids"]),
        "reached_removed_vs_G0": sorted(set(baseline["reached_mutant_ids"]) - reached),
        "killed_removed_vs_G0": sorted(set(baseline["killed_mutant_ids"]) - killed),
        "reached_added_vs_G0": sorted(reached - set(baseline["reached_mutant_ids"])),
        "killed_added_vs_G0": sorted(killed - set(baseline["killed_mutant_ids"])),
    }

g3_excluded = [row for row in units if not row["gates"]["G3_FULL_TASK_EFFECT_CERTIFICATE"]]
excluded_breakdown = Counter(row["source_phase"] for row in g3_excluded)
excluded_claims = Counter(
    row["source_phase"] for row in g3_excluded if row["official_pass_claim"]
)

feasibility = []
for case_dir in sorted((ROOT / "artifacts" / "phase2_4" / "cases").iterdir()):
    if not case_dir.is_dir():
        continue
    metadata = load(case_dir / "metadata.json")
    evidence = load(case_dir / "evaluator_predicate_evidence.json")
    trace_path = evidence["official_evaluation_path"] == "utility_from_traces"
    field = metadata["state_effect_field"]
    direct_weakening = field in {"body", "password"} or evidence["evaluator_explicitly_checks_target_field"]
    if trace_path:
        drop_status = "CONDITIONAL_COMBINATOR_DECOMPOSITION_REQUIRED"
        weaken_status = "CONDITIONAL_COMBINATOR_DECOMPOSITION_REQUIRED"
    else:
        drop_status = "FEASIBLE_DIRECT_PREDICATE_MUTATION"
        weaken_status = (
            "FEASIBLE_DIRECT_PREDICATE_MUTATION"
            if direct_weakening
            else "FEASIBLE_COLLECTION_PROJECTION_MUTATION"
        )
    feasibility.append(
        {
            "case_id": case_dir.name,
            "suite": metadata["suite"],
            "task_id": metadata["task_id"],
            "target_field": field,
            "target_argument": metadata["target_argument"],
            "official_evaluation_path": evidence["official_evaluation_path"],
            "utility_function": evidence["utility_function"],
            "utility_source_file": evidence["utility_source_file"],
            "utility_source_line": evidence["utility_source_line"],
            "drop_required_value_check": drop_status,
            "weaken_value_to_existence": weaken_status,
            "feasibility_basis": (
                "mutate the named state predicate while retaining all other utility conditions"
                if not trace_path
                else "the official trace/combinator path must be decomposed before an equivalent atom can be patched"
            ),
            "mutant_executed": False,
        }
    )

instance_counts = Counter()
for row in feasibility:
    for key in ["drop_required_value_check", "weaken_value_to_existence"]:
        instance_counts[row[key]] += 1

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "cross_tabs.json").write_text(json.dumps(cross_tabs, indent=2, sort_keys=True) + "\n")
(OUT / "mutant_id_sets.json").write_text(json.dumps(sets, indent=2, sort_keys=True) + "\n")
(OUT / "mutant_set_comparison.json").write_text(json.dumps(set_comparison, indent=2, sort_keys=True) + "\n")
(OUT / "agentdojo_adapter_feasibility.jsonl").write_text(
    "".join(json.dumps(row, sort_keys=True) + "\n" for row in feasibility)
)
results = {
    "phase": "E2-STRUCTURAL",
    "retrospective_ablation": True,
    "cross_tabs": cross_tabs,
    "full_gate_exclusions": {
        "total": len(g3_excluded),
        "by_source_phase": dict(sorted(excluded_breakdown.items())),
        "claims_by_source_phase": dict(sorted(excluded_claims.items())),
        "independently_labeled": sum(row["independent_claim_audit"] is not None for row in g3_excluded),
        "semantic_label_missing": sum(row["independent_claim_audit"] is None for row in g3_excluded),
    },
    "mutant_identity_comparison": set_comparison,
    "agentdojo_adapter_feasibility": {
        "cases": len(feasibility),
        "fault_instances": len(feasibility) * 2,
        "by_status": dict(sorted(instance_counts.items())),
        "fully_feasible_cases": sum(
            not row["drop_required_value_check"].startswith("CONDITIONAL")
            and not row["weaken_value_to_existence"].startswith("CONDITIONAL")
            for row in feasibility
        ),
        "conditional_cases": sum(
            row["drop_required_value_check"].startswith("CONDITIONAL")
            or row["weaken_value_to_existence"].startswith("CONDITIONAL")
            for row in feasibility
        ),
        "mutants_executed": 0,
    },
    "new_trajectory_executions": 0,
    "llm_api_calls": 0,
}
(OUT / "results.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
print(json.dumps(results, indent=2, sort_keys=True))
