#!/usr/bin/env python3
"""Run modeled-fault controls over persisted CERTCF evidence.

This module does not mutate or re-execute an official evaluator source. The
legacy ``mutant`` identifiers are retained only for historical ledger
compatibility.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "validation_e1"


def load(path: Path) -> Any:
    return json.loads(path.read_text())


def digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows))


def official_pass(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    if payload.get("official_verdict") is True:
        return True
    if payload.get("verdict") == "PASS":
        return True
    result = payload.get("result")
    return bool(payload.get("status") == "COMPLETED" and isinstance(result, dict) and result.get("success") is True)


def find_effect_payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if isinstance(value.get("diff"), list) and "result_db_hash" in value and "golden_db_hash" in value:
            return value
        for child in value.values():
            found = find_effect_payload(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_effect_payload(child)
            if found is not None:
                return found
    return None


def path_matches_field(path: str, field: str) -> bool:
    return path == field or path.endswith("." + field) or path.endswith("]." + field)


def drop_required_conjunct(diff: list[dict[str, Any]], target_field: str) -> bool:
    """Mutated exact-state oracle: ignore exactly one target-field mismatch."""
    remaining = [item for item in diff if not path_matches_field(str(item.get("path", "")), target_field)]
    return len(remaining) == 0


def weaken_value_to_existence(diff: list[dict[str, Any]], target_field: str) -> bool:
    """Mutated exact-state oracle: target field must exist but need not equal its golden value."""
    target = [item for item in diff if path_matches_field(str(item.get("path", "")), target_field)]
    target_exists = bool(target) and all(item.get("result") is not None for item in target)
    remaining = [item for item in diff if item not in target]
    return target_exists and len(remaining) == 0


def all_to_any(values: list[bool]) -> bool:
    return any(values)


def drop_effect(values: dict[str, bool], effect_id: str) -> bool:
    return all(value for key, value in values.items() if key != effect_id)


def classify(valid: bool, activated: bool, unexpected: bool) -> str:
    if not valid:
        return "INVALID_MUTANT"
    if not activated:
        return "UNCOVERED"
    return "KILLED" if unexpected else "SURVIVED"


def thinkingbox_r2a_mutants() -> list[dict[str, Any]]:
    population = load(ROOT / "artifacts" / "phase3_2" / "POPULATION_V1.json")
    case_dirs = sorted((ROOT / "artifacts" / "phase3_2" / "cases").iterdir())
    cases: dict[tuple[str, int, str], Path] = {}
    for case_dir in case_dirs:
        metadata = load(case_dir / "metadata.json")
        key = (metadata["task_uid"], metadata["target_call_index"], metadata["target_argument"])
        cases[key] = case_dir

    rows: list[dict[str, Any]] = []
    for candidate in population:
        key = (
            candidate["task_uid"],
            candidate["target_call"]["index"],
            candidate["target_argument"]["name"],
        )
        case_dir = cases[key]
        metadata = load(case_dir / "metadata.json")
        certificate = load(case_dir / "task_critical_effect_certificate.json")
        original_eval = load(case_dir / "original" / "evaluator.json")
        perturbed_eval = load(case_dir / "perturbed" / "evaluator.json")
        field = candidate["state_effect"]["field"]
        effect_payload = find_effect_payload(perturbed_eval.get("effects"))
        diff = effect_payload.get("diff", []) if effect_payload else []
        certified = certificate["classification"] == "TASK_CRITICAL_VIOLATION_CERTIFIED"
        target_reached = certified and any(path_matches_field(str(item.get("path", "")), field) for item in diff)
        baseline = official_pass(original_eval)

        for fault_class, mutant_pass in (
            ("DROP_REQUIRED_CONJUNCT", drop_required_conjunct(diff, field)),
            ("WEAKEN_VALUE_TO_EXISTENCE", weaken_value_to_existence(diff, field)),
        ):
            valid = baseline
            activated = valid and target_reached
            status = classify(valid, activated, activated and mutant_pass)
            atom = {
                "task_uid": candidate["task_uid"],
                "field": field,
                "call_index": candidate["target_call"]["index"],
                "argument": candidate["target_argument"]["name"],
            }
            rows.append(
                {
                    "mutant_id": digest(["thinkingbox", fault_class, atom]),
                    "benchmark": "ThinkingBox",
                    "relation_evidence": "R2A",
                    "fault_class": fault_class,
                    "task_id": candidate["task_id"],
                    "atom": atom,
                    "official_evaluator_path": "validate_database exact final-state hash",
                    "source_level_mutation": (
                        "filter one frozen field path from the exact-state diff before asserting equality"
                        if fault_class == "DROP_REQUIRED_CONJUNCT"
                        else "replace one frozen field equality with field existence before asserting equality"
                    ),
                    "baseline_preserved": baseline,
                    "certified_counterfactual_available": certified,
                    "activated": activated,
                    "official_counterfactual_pass": official_pass(perturbed_eval),
                    "mutant_counterfactual_pass": mutant_pass if activated else None,
                    "status": status,
                    "evidence": {
                        "case_id": metadata["case_id"],
                        "target_field": field,
                        "official_diff": diff,
                        "certificate": certificate["classification"],
                    },
                }
            )
    return rows


def r3_case_values(case_dir: Path, phase: str) -> tuple[list[dict[str, Any]], dict[str, bool], dict[str, bool]]:
    predicates = load(case_dir / "effect_predicates.json")
    if phase == "phase4_1":
        certificate = load(case_dir / "conjunctive_omission_certificate.json")
        original = {item["effect_id"]: bool(item["satisfied"]) for item in certificate.get("original_predicates", [])}
        omission = {item["effect_id"]: bool(item["satisfied"]) for item in certificate.get("omission_predicates", [])}
    else:
        original_results = load(case_dir / "original" / "effects.json")
        omission_results = load(case_dir / "omission" / "effects.json")
        ids = [f"{case_dir.name}::effect-{index}" for index in range(len(predicates))]
        for index, predicate in enumerate(predicates):
            predicate.setdefault("effect_id", ids[index])
        original = {predicate["effect_id"]: bool(result["result"]) for predicate, result in zip(predicates, original_results)}
        omission = {predicate["effect_id"]: bool(result["result"]) for predicate, result in zip(predicates, omission_results)}
    return predicates, original, omission


def r3_mutants() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for phase, benchmark in (("phase4_1", "ThinkingBox"), ("phase4_6", "AppWorld")):
        for case_dir in sorted((ROOT / "artifacts" / phase / "cases").iterdir()):
            certificate = load(case_dir / "conjunctive_omission_certificate.json")
            if certificate.get("classification") != "CONJUNCTIVE_OMISSION_CERTIFIED":
                continue
            metadata = load(case_dir / "metadata.json")
            predicates, original, omission = r3_case_values(case_dir, phase)
            original_eval = load(case_dir / "original" / "evaluator.json")
            omission_eval = load(case_dir / "omission" / "evaluator.json")
            baseline = official_pass(original_eval) and bool(original) and all(original.values())
            omitted_id = metadata["omitted_effect_id"]
            if phase == "phase4_6":
                target_index = next(
                    index
                    for index, predicate in enumerate(metadata["all_effect_predicates"])
                    if predicate["model"] == metadata["original_effect_witness"]["model"]
                    and predicate["record_id"] == metadata["original_effect_witness"]["record_id"]
                )
                omitted_id = predicates[target_index]["effect_id"]

            for predicate in predicates:
                effect_id = predicate["effect_id"]
                activated = baseline and effect_id == omitted_id and effect_id in omission
                mutant_pass = drop_effect(omission, effect_id) if activated else None
                rows.append(
                    {
                        "mutant_id": digest([benchmark, metadata["task_id"], "DROP_REQUIRED_CONJUNCT", effect_id]),
                        "benchmark": benchmark,
                        "relation_evidence": "R3",
                        "fault_class": "DROP_REQUIRED_CONJUNCT",
                        "task_id": metadata["task_id"],
                        "atom": {"effect_id": effect_id},
                        "official_evaluator_path": "deterministic final-state requirement conjunction",
                        "source_level_mutation": "remove exactly one independently represented effect predicate",
                        "baseline_preserved": baseline,
                        "certified_counterfactual_available": True,
                        "activated": activated,
                        "official_counterfactual_pass": official_pass(omission_eval),
                        "mutant_counterfactual_pass": mutant_pass,
                        "status": classify(baseline, activated, bool(mutant_pass)),
                        "evidence": {"case_id": case_dir.name, "original_effects": original, "omission_effects": omission},
                    }
                )

            mixed = bool(omission) and any(omission.values()) and not all(omission.values())
            mutant_pass = all_to_any(list(omission.values())) if mixed else None
            activated = baseline and mixed
            rows.append(
                {
                    "mutant_id": digest([benchmark, metadata["task_id"], "ALL_TO_ANY"]),
                    "benchmark": benchmark,
                    "relation_evidence": "R3",
                    "fault_class": "ALL_TO_ANY",
                    "task_id": metadata["task_id"],
                    "atom": {"effect_ids": sorted(original)},
                    "official_evaluator_path": "deterministic final-state requirement conjunction",
                    "source_level_mutation": "replace all required effect predicates with any required effect predicate",
                    "baseline_preserved": baseline,
                    "certified_counterfactual_available": True,
                    "activated": activated,
                    "official_counterfactual_pass": official_pass(omission_eval),
                    "mutant_counterfactual_pass": mutant_pass,
                    "status": classify(baseline, activated, bool(mutant_pass)),
                    "evidence": {"case_id": case_dir.name, "original_effects": original, "omission_effects": omission},
                }
            )
    return rows


def agentdojo_r1_mutants() -> list[dict[str, Any]]:
    population = load(ROOT / "artifacts" / "phase1_4" / "POPULATION_V1.json")
    by_task: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in population:
        case_dir = ROOT / "artifacts" / "phase1_4" / "cases" / record["candidate_id"]
        by_task[(record["suite"], record["task_id"])].append(
            {
                "record": record,
                "metadata": load(case_dir / "metadata.json"),
                "certificate": load(case_dir / "validity_certificate.json"),
                "original_eval": load(case_dir / "original" / "evaluator.json") if (case_dir / "original" / "evaluator.json").exists() else None,
                "transformed_eval": load(case_dir / "transformed" / "evaluator.json") if (case_dir / "transformed" / "evaluator.json").exists() else None,
            }
        )

    rows: list[dict[str, Any]] = []
    for (suite, task_id), cases in sorted(by_task.items()):
        baseline = any(official_pass(case["original_eval"]) for case in cases)
        certified = [case for case in cases if case["certificate"]["classification"] == "VALIDITY_CERTIFIED"]
        activated = baseline and bool(certified)
        mutant_pass = False if activated else None
        rows.append(
            {
                "mutant_id": digest(["AgentDojo", suite, task_id, "REFERENCE_TRACE_OVERFIT"]),
                "benchmark": "AgentDojo",
                "relation_evidence": "R1",
                "fault_class": "REFERENCE_TRACE_OVERFIT",
                "task_id": task_id,
                "suite": suite,
                "atom": {"reference_order": "exact official ground-truth order"},
                "official_evaluator_path": "state-based utility",
                "source_level_mutation": "conjoin state utility with exact reference interaction order",
                "baseline_preserved": baseline,
                "certified_counterfactual_available": bool(certified),
                "activated": activated,
                "official_counterfactual_pass": all(official_pass(case["transformed_eval"]) for case in certified) if certified else None,
                "mutant_counterfactual_pass": mutant_pass,
                "status": classify(baseline, activated, activated and mutant_pass is False),
                "evidence": {
                    "certified_pairs": len(certified),
                    "candidate_pairs": len(cases),
                    "evaluator_path": "utility",
                },
            }
        )
    return rows


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status = Counter(row["status"] for row in rows)
    by_fault: dict[str, Counter[str]] = defaultdict(Counter)
    by_benchmark: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        by_fault[row["fault_class"]][row["status"]] += 1
        by_fault[row["fault_class"]]["total"] += 1
        by_benchmark[row["benchmark"]][row["status"]] += 1
        by_benchmark[row["benchmark"]]["total"] += 1
    activated = status["KILLED"] + status["SURVIVED"]
    valid = len(rows) - status["INVALID_MUTANT"]
    return {
        "phase": "E1",
        "control_type": "seeded evaluator faults on persisted certified evidence",
        "official_benchmark_defects_claimed": 0,
        "mutants_total": len(rows),
        "mutants_valid": valid,
        "mutants_activated": activated,
        "mutants_killed": status["KILLED"],
        "mutants_survived": status["SURVIVED"],
        "mutants_uncovered": status["UNCOVERED"],
        "mutants_invalid": status["INVALID_MUTANT"],
        "mutation_score_activated": status["KILLED"] / activated if activated else None,
        "certified_test_coverage_of_valid_mutants": activated / valid if valid else None,
        "by_fault_class": {key: dict(value) for key, value in sorted(by_fault.items())},
        "by_benchmark": {key: dict(value) for key, value in sorted(by_benchmark.items())},
        "trajectory_executions": 0,
        "llm_api_calls": 0,
        "prior_artifacts_modified": False,
    }


def main() -> None:
    receipt = load(OUT / "FREEZE_RECEIPT.json")
    if receipt["fault_model_sha256"] != hashlib.sha256((OUT / "FAULT_MODEL_V1.json").read_bytes()).hexdigest():
        raise RuntimeError("frozen fault model digest mismatch")
    rows = thinkingbox_r2a_mutants() + r3_mutants() + agentdojo_r1_mutants()
    results = aggregate(rows)
    append_jsonl(OUT / "mutants.jsonl", rows)
    write_json(OUT / "results.json", results)
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
