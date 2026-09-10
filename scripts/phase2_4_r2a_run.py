#!/usr/bin/env python3
"""Execute and audit the complete frozen R2A held-out calibration population."""

from __future__ import annotations

import ast
import copy
import inspect
import json
import textwrap
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from agentdojo.base_tasks import BaseUserTask

import phase1_3_agentdojo_run as protocol
from phase1_3_agentdojo_common import ROOT, call_data, canonical_bytes, canonical_value, digest, file_digest, write_json
from phase1_3_retry1_agentdojo_run import retry1_evaluate
from phase2_3_scan_r2a_effects import CAUSAL_FIELDS, effect_witness

OUT = ROOT / "artifacts" / "phase2_4"


def verify_freeze() -> list[dict[str, Any]]:
    receipt = json.loads((OUT / "FREEZE_RECEIPT.json").read_text())
    paths = {
        "phase2_3_candidate_artifact_sha256": ROOT / "artifacts/phase2_3/r2a_effect_candidates.jsonl",
        "heldout_population_sha256": OUT / "HELDOUT_POPULATION_V1.json",
        "mutation_rules_sha256": OUT / "MUTATION_RULES_V1.json",
        "execution_runner_sha256": Path(__file__),
        "instrumentation_sha256": ROOT / "scripts/phase1_3_agentdojo_common.py",
        "adapter_sha256": ROOT / "scripts/phase1_3_retry1_adapter.py",
        "state_canonicalization_sha256": ROOT / "artifacts/phase1_2/STATE_CANONICALIZATION_V1.txt",
        "response_canonicalization_sha256": ROOT / "artifacts/phase1_2/RESPONSE_CANONICALIZATION_V1.txt",
        "task_critical_effect_specification_sha256": OUT / "TASK_CRITICAL_EFFECT_SPECIFICATION_V1.txt",
        "semantic_violation_certificate_specification_sha256": OUT / "SEMANTIC_VIOLATION_CERTIFICATE_SPECIFICATION_V1.txt",
        "evaluator_relation_sha256": OUT / "EVALUATOR_RELATION_SPECIFICATION_V1.txt",
    }
    for field, path in paths.items():
        if file_digest(path) != receipt[field]:
            raise RuntimeError(f"Phase 2.4 freeze verification failed: {field}")
    population = json.loads((OUT / "HELDOUT_POPULATION_V1.json").read_text())
    if len(population) != 9 or len({(record["suite"], record["task_id"]) for record in population}) != 9:
        raise RuntimeError("Held-out population identity changed")
    return population


def adapted(record: dict[str, Any]) -> dict[str, Any]:
    return {**record, "call_index": record["target_call_index"], "argument_name": record["target_argument"], "original_value": record["literal"]}


def exact_mutation(trajectory: list[Any], record: dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
    before = [call_data(call) for call in trajectory]
    transformed = copy.deepcopy(trajectory)
    index, name = record["target_call_index"], record["target_argument"]
    if index >= len(transformed) or name not in transformed[index].args:
        return transformed, {"status": "MUTATION_EXACTNESS_FAILED", "reason": "target_unavailable"}
    new_args = copy.deepcopy(transformed[index].args)
    new_args[name] = copy.deepcopy(record["proposed_value"])
    transformed[index] = transformed[index].model_copy(update={"args": new_args}, deep=True)
    after = [call_data(call) for call in transformed]
    changed_calls = [i for i, (left, right) in enumerate(zip(before, after)) if canonical_bytes(left) != canonical_bytes(right)]
    changed_args = [key for key in set(before[index]["arguments"]) | set(after[index]["arguments"]) if canonical_bytes(before[index]["arguments"].get(key)) != canonical_bytes(after[index]["arguments"].get(key))]
    checks = {
        "trajectory_length_unchanged": len(before) == len(after),
        "function_identities_unchanged": [item["function"] for item in before] == [item["function"] for item in after],
        "call_ordering_unchanged": True,
        "only_target_call_changed": changed_calls == [index],
        "exactly_target_argument_changed": changed_args == [name],
        "original_argument_equals_L": canonical_bytes(before[index]["arguments"][name]) == canonical_bytes(record["literal"]),
        "transformed_argument_equals_frozen_L_prime": canonical_bytes(after[index]["arguments"][name]) == canonical_bytes(record["proposed_value"]),
        "L_prime_differs_from_L": canonical_bytes(record["literal"]) != canonical_bytes(record["proposed_value"]),
        "all_other_calls_and_arguments_unchanged": changed_calls == [index] and changed_args == [name],
    }
    return transformed, {"certificate_type": "MUTATION_EXACTNESS_CERTIFICATE", "status": "PASS" if all(checks.values()) else "MUTATION_EXACTNESS_FAILED", "checks": checks, "changed_call_positions": changed_calls, "changed_arguments": changed_args}


def trajectory_compensation(trajectory: list[Any], record: dict[str, Any]) -> list[dict[str, Any]]:
    expected_mapping = CAUSAL_FIELDS[(record["function"], record["target_argument"])]
    matches = []
    for index, call in enumerate(trajectory):
        for name, value in call.args.items():
            if index == record["target_call_index"] and name == record["target_argument"]:
                continue
            if CAUSAL_FIELDS.get((call.function, name)) == expected_mapping and canonical_bytes(value) == canonical_bytes(record["literal"]):
                matches.append({"call_index": index, "function": call.function, "argument": name})
    return matches


def evaluator_evidence(task: Any, record: dict[str, Any]) -> dict[str, Any]:
    source = inspect.getsource(type(task).utility)
    tree = ast.parse(textwrap.dedent(source))
    attributes = sorted({node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)})
    target_field = record["state_effect_field"]
    literal = str(record["literal"])
    overridden = type(task).utility_from_traces is not BaseUserTask.utility_from_traces
    return {
        "official_evaluation_path": "utility_from_traces" if overridden else "utility",
        "utility_from_traces_implemented": overridden,
        "utility_source_file": inspect.getsourcefile(type(task)),
        "utility_source_line": inspect.getsourcelines(type(task).utility)[1],
        "utility_function": f"{type(task).__name__}.utility",
        "utility_source": source,
        "attribute_names_read_by_utility_ast": attributes,
        "target_required_effect_field": target_field,
        "target_field_name_explicitly_present": target_field in source,
        "required_literal_explicitly_present": literal.casefold() in source.casefold(),
        "evaluator_explicitly_checks_target_field": target_field in source and literal.casefold() in source.casefold(),
    }


def persist_branch(base: Path, trajectory: list[Any], initial: Any, execution: dict[str, Any], evaluator: dict[str, Any] | None) -> None:
    write_json(base / "trajectory.json", [call_data(call) for call in trajectory])
    write_json(base / "execution.json", {"status": execution["status"], "exception": execution["exception"]})
    write_json(base / "response_trace.json", execution["response_trace"])
    write_json(base / "function_stack.json", execution["function_stack"])
    write_json(base / "initial_state.json", initial)
    (base / "initial_state.sha256").write_text(digest(initial) + "\n")
    write_json(base / "final_state.json", execution["final_state"])
    (base / "final_state.sha256").write_text(execution["final_state_digest"] + "\n")
    write_json(base / "final_output.json", execution["final_output"])
    if evaluator is not None:
        write_json(base / "evaluator.json", evaluator)


def certificate(record: dict[str, Any], original: dict[str, Any], perturbed: dict[str, Any], initial_o: Any, initial_t: Any, exactness: dict[str, Any], original_witness: dict[str, Any] | None, wrong_witness: dict[str, Any] | None, correct_witness: dict[str, Any] | None, compensation: list[dict[str, Any]]) -> dict[str, Any]:
    index = record["target_call_index"]
    target_success = perturbed["status"] == "SUCCESS" and len(perturbed["response_trace"]) > index and perturbed["response_trace"][index].get("error") is None and perturbed["response_trace"][index].get("exception") is None
    checks = {
        "explicit_requirement_role_frozen": bool(record["semantic_role"]),
        "original_action_uses_L": canonical_bytes(original["response_trace"][index]["arguments"][record["target_argument"]]) == canonical_bytes(record["literal"]) if len(original["response_trace"]) > index else False,
        "original_effect_witness_reproduced": original_witness is not None,
        "exact_mutation": exactness["status"] == "PASS",
        "perturbed_action_uses_L_prime": canonical_bytes(perturbed["response_trace"][index]["arguments"][record["target_argument"]]) == canonical_bytes(record["proposed_value"]) if len(perturbed["response_trace"]) > index else False,
        "perturbed_target_action_success": target_success,
        "wrong_persistent_effect_witness_L_prime": wrong_witness is not None,
        "required_correct_effect_L_absent": correct_witness is None,
        "no_trajectory_compensation": not compensation,
        "unique_effect_producer_frozen": record["unique_producer_evidence"]["count"] == 1,
        "semantic_role_unchanged": record["semantic_role"]["semantic_role"] == record["target_argument"],
        "independent_initial_states_equal": digest(initial_o) == digest(initial_t),
    }
    evidence = {"checks": checks, "original_effect_witness": original_witness, "wrong_effect_witness": wrong_witness, "correct_effect_witness_in_perturbed_state": correct_witness, "trajectory_compensation": compensation}
    if not target_success:
        return {"classification": "TASK_CRITICAL_VIOLATION_UNKNOWN", "reason": "MUTATION_EXECUTION_INVALID", **evidence}
    if correct_witness is not None or compensation:
        return {"classification": "COMPENSATED_EFFECT", "reason": "correct_or_equivalent_effect_present", **evidence}
    if wrong_witness is None:
        if original_witness is not None:
            return {"classification": "BEHAVIORALLY_EQUIVALENT_MUTATION", "reason": "wrong_persistent_effect_not_observed", **evidence}
        return {"classification": "TASK_CRITICAL_VIOLATION_UNKNOWN", "reason": "effect_evidence_unavailable", **evidence}
    if all(checks.values()):
        return {"classification": "TASK_CRITICAL_VIOLATION_CERTIFIED", "reason": None, **evidence}
    return {"classification": "TASK_CRITICAL_VIOLATION_FAILED", "reason": "certificate_check_failed", **evidence}


def run_case(record: dict[str, Any]) -> None:
    case_dir = OUT / "cases" / record["candidate_id"]
    if case_dir.exists():
        raise FileExistsError(case_dir)
    runtime_record = adapted(record)
    suite_o, task_o, trajectory_o, environment_o = protocol.fresh_context(runtime_record)
    suite_t, task_t, trajectory_t_base, environment_t = protocol.fresh_context(runtime_record)
    initial_o, initial_t = environment_o.model_copy(deep=True), environment_t.model_copy(deep=True)
    trajectory_t, exactness = exact_mutation(trajectory_t_base, record)
    write_json(case_dir / "metadata.json", {key: record[key] for key in ("candidate_id", "suite", "task_id", "task_version", "argument_type", "state_effect_entity", "state_effect_field", "target_call_index", "target_argument", "literal", "proposed_value", "deterministic_mutation_rule", "semantic_role")})
    write_json(case_dir / "mutation_exactness_certificate.json", exactness)
    original = protocol.execute(suite_o, task_o, trajectory_o, environment_o)
    original_eval = retry1_evaluate(suite_o, task_o, initial_o, original) if original["status"] == "SUCCESS" else {"official_verdict": False, "reason": "execution_failed"}
    original_witness = effect_witness(initial_o, original["final_state"], record) if original["status"] == "SUCCESS" else None
    persist_branch(case_dir / "original", trajectory_o, initial_o, original, original_eval)
    write_json(case_dir / "original/effect_witness.json", original_witness)
    if original["status"] != "SUCCESS" or original_eval.get("official_verdict") is not True or original_witness is None:
        write_json(case_dir / "task_critical_violation_certificate.json", {"classification": "TASK_CRITICAL_VIOLATION_UNKNOWN", "reason": "BASELINE_EFFECT_REPLAY_FAILED", "original_effect_witness": original_witness})
        return
    if exactness["status"] != "PASS":
        write_json(case_dir / "task_critical_violation_certificate.json", {"classification": "TASK_CRITICAL_VIOLATION_UNKNOWN", "reason": "MUTATION_EXACTNESS_FAILED"})
        return
    perturbed = protocol.execute(suite_t, task_t, trajectory_t, environment_t)
    wrong_record = {**record, "literal": record["proposed_value"]}
    wrong_witness = effect_witness(initial_t, perturbed["final_state"], wrong_record) if perturbed["status"] == "SUCCESS" else None
    correct_witness = effect_witness(initial_t, perturbed["final_state"], record) if perturbed["status"] == "SUCCESS" else None
    compensation = trajectory_compensation(trajectory_t, record)
    cert = certificate(record, original, perturbed, initial_o, initial_t, exactness, original_witness, wrong_witness, correct_witness, compensation)
    write_json(case_dir / "task_critical_violation_certificate.json", cert)
    evaluator = None
    predicate = evaluator_evidence(task_t, record)
    write_json(case_dir / "evaluator_predicate_evidence.json", predicate)
    if cert["classification"] == "TASK_CRITICAL_VIOLATION_CERTIFIED":
        evaluator = retry1_evaluate(suite_t, task_t, initial_t, perturbed)
        evaluator["sensitivity_classification"] = "SENSITIVE" if evaluator["official_verdict"] is False else "R2A_FALSE_ACCEPTANCE_CANDIDATE"
        evaluator["predicate_evidence"] = predicate
    persist_branch(case_dir / "perturbed", trajectory_t, initial_t, perturbed, evaluator)
    write_json(case_dir / "perturbed/wrong_effect_witness.json", wrong_witness)
    write_json(case_dir / "perturbed/correct_effect_witness.json", correct_witness)


def audit_false_acceptance(record: dict[str, Any]) -> dict[str, Any]:
    runtime_record = adapted(record)
    suite_o, task_o, trajectory_o, environment_o = protocol.fresh_context(runtime_record)
    suite_t, task_t, trajectory_t_base, environment_t = protocol.fresh_context(runtime_record)
    initial_o, initial_t = environment_o.model_copy(deep=True), environment_t.model_copy(deep=True)
    trajectory_t, exactness = exact_mutation(trajectory_t_base, record)
    original = protocol.execute(suite_o, task_o, trajectory_o, environment_o)
    perturbed = protocol.execute(suite_t, task_t, trajectory_t, environment_t)
    original_eval = retry1_evaluate(suite_o, task_o, initial_o, original) if original["status"] == "SUCCESS" else {"official_verdict": False}
    perturbed_eval = retry1_evaluate(suite_t, task_t, initial_t, perturbed) if perturbed["status"] == "SUCCESS" else {"official_verdict": False}
    original_witness = effect_witness(initial_o, original["final_state"], record) if original["status"] == "SUCCESS" else None
    wrong_witness = effect_witness(initial_t, perturbed["final_state"], {**record, "literal": record["proposed_value"]}) if perturbed["status"] == "SUCCESS" else None
    correct_witness = effect_witness(initial_t, perturbed["final_state"], record) if perturbed["status"] == "SUCCESS" else None
    compensation = trajectory_compensation(trajectory_t, record)
    predicate = evaluator_evidence(task_t, record)
    checks = {
        "explicit_user_effect_requirement": bool(record["semantic_role"]),
        "original_effect_correct": original_witness is not None,
        "mutation_exact": exactness["status"] == "PASS",
        "perturbed_action_successful": perturbed["status"] == "SUCCESS" and wrong_witness is not None,
        "wrong_persistent_effect_exists": wrong_witness is not None,
        "correct_effect_absent": correct_witness is None,
        "no_compensation": not compensation,
        "semantic_role_unambiguous": record["semantic_role"]["semantic_role"] == record["target_argument"],
        "original_pass_reproduced": original_eval.get("official_verdict") is True,
        "perturbed_pass_reproduced": perturbed_eval.get("official_verdict") is True,
    }
    if all(checks.values()):
        classification = "CONFIRMED_R2A_FALSE_ACCEPTANCE"
    elif not checks["semantic_role_unambiguous"]:
        classification = "TASK_SEMANTICS_AMBIGUOUS"
    elif not all(checks[key] for key in ("original_effect_correct", "mutation_exact", "perturbed_action_successful", "wrong_persistent_effect_exists", "correct_effect_absent", "no_compensation")):
        classification = "EFFECT_CERTIFICATE_INVALID"
    elif checks["perturbed_pass_reproduced"]:
        classification = "EVALUATOR_ACCEPTANCE_JUSTIFIED"
    else:
        classification = "AUDIT_UNKNOWN"
    audit = {"candidate_id": record["candidate_id"], "checks": checks, "predicate_evidence": predicate, "original_effect_witness": original_witness, "wrong_effect_witness": wrong_witness, "correct_effect_witness": correct_witness, "trajectory_compensation": compensation, "final_classification": classification}
    base = OUT / "cases" / record["candidate_id"] / "audit_reproduction"
    persist_branch(base / "original", trajectory_o, initial_o, original, original_eval)
    persist_branch(base / "perturbed", trajectory_t, initial_t, perturbed, perturbed_eval)
    write_json(base / "audit.json", audit)
    return audit


def load_case(record: dict[str, Any]) -> dict[str, Any]:
    base = OUT / "cases" / record["candidate_id"]
    return {
        "record": record,
        "certificate": json.loads((base / "task_critical_violation_certificate.json").read_text()),
        "exactness": json.loads((base / "mutation_exactness_certificate.json").read_text()),
        "original_execution": json.loads((base / "original/execution.json").read_text()) if (base / "original/execution.json").exists() else None,
        "original_evaluator": json.loads((base / "original/evaluator.json").read_text()) if (base / "original/evaluator.json").exists() else None,
        "original_witness": json.loads((base / "original/effect_witness.json").read_text()) if (base / "original/effect_witness.json").exists() else None,
        "perturbed_execution": json.loads((base / "perturbed/execution.json").read_text()) if (base / "perturbed/execution.json").exists() else None,
        "perturbed_evaluator": json.loads((base / "perturbed/evaluator.json").read_text()) if (base / "perturbed/evaluator.json").exists() else None,
    }


def aggregate(population: list[dict[str, Any]], audits: list[dict[str, Any]]) -> dict[str, Any]:
    cases = [load_case(record) for record in population]
    classes = Counter(case["certificate"]["classification"] for case in cases)
    relations = Counter(case["perturbed_evaluator"].get("sensitivity_classification") for case in cases if case["perturbed_evaluator"])
    audit_classes = Counter(audit["final_classification"] for audit in audits)
    strata: dict[str, dict[str, dict[str, int]]] = {"effect_type": defaultdict(lambda: defaultdict(int)), "argument_type": defaultdict(lambda: defaultdict(int)), "suite": defaultdict(lambda: defaultdict(int))}
    for case in cases:
        record = case["record"]
        keys = {"effect_type": f"{record['state_effect_entity']}.{record['state_effect_field']}", "argument_type": record["argument_type"], "suite": record["suite"]}
        for dimension, key in keys.items():
            bucket = strata[dimension][key]
            bucket["attempted"] += 1
            bucket[case["certificate"]["classification"]] += 1
            if case["perturbed_evaluator"]:
                bucket[case["perturbed_evaluator"]["sensitivity_classification"]] += 1
    certified = classes["TASK_CRITICAL_VIOLATION_CERTIFIED"]
    return {
        "heldout_population": 9,
        "attempted": len(cases),
        "baseline_replay_success": sum(case["original_execution"] and case["original_execution"]["status"] == "SUCCESS" and case["original_evaluator"] and case["original_evaluator"].get("official_verdict") is True for case in cases),
        "original_effect_witness_reproduced": sum(case["original_witness"] is not None for case in cases),
        "mutation_exactness_success": sum(case["exactness"]["status"] == "PASS" for case in cases),
        "perturbed_execution_success": sum(case["perturbed_execution"] and case["perturbed_execution"]["status"] == "SUCCESS" for case in cases),
        "TASK_CRITICAL_VIOLATION_CERTIFIED": certified,
        "TASK_CRITICAL_VIOLATION_FAILED": classes["TASK_CRITICAL_VIOLATION_FAILED"],
        "TASK_CRITICAL_VIOLATION_UNKNOWN": classes["TASK_CRITICAL_VIOLATION_UNKNOWN"],
        "BEHAVIORALLY_EQUIVALENT_MUTATION": classes["BEHAVIORALLY_EQUIVALENT_MUTATION"],
        "COMPENSATED_EFFECT": classes["COMPENSATED_EFFECT"],
        "evaluator_comparisons": sum(case["perturbed_evaluator"] is not None for case in cases),
        "SENSITIVE": relations["SENSITIVE"],
        "R2A_FALSE_ACCEPTANCE_CANDIDATE": relations["R2A_FALSE_ACCEPTANCE_CANDIDATE"],
        "independent_audit": dict(audit_classes),
        "stratification": {dimension: {key: dict(value) for key, value in sorted(buckets.items())} for dimension, buckets in strata.items()},
        "exclusion_reasons": dict(Counter(case["certificate"].get("reason") for case in cases if case["certificate"].get("reason"))),
        "agentdojo_role": "CALIBRATION_TARGET",
        "llm_api_calls": 0,
        "issue_pr_search_performed": False,
    }


def main() -> None:
    population = verify_freeze()
    protocol.OUT = OUT
    protocol.evaluate = retry1_evaluate
    for record in population:
        run_case(record)
    initial_cases = [load_case(record) for record in population]
    false_records = [case["record"] for case in initial_cases if case["perturbed_evaluator"] and case["perturbed_evaluator"].get("sensitivity_classification") == "R2A_FALSE_ACCEPTANCE_CANDIDATE"]
    audits = [audit_false_acceptance(record) for record in false_records]
    if audits:
        write_json(OUT / "AUDIT_RESULTS.json", {"candidate_count": len(audits), "audits": audits, "classification_counts": dict(Counter(audit["final_classification"] for audit in audits))})
    results = aggregate(population, audits)
    write_json(OUT / "results.json", results)
    rows = []
    audit_map = {audit["candidate_id"]: audit["final_classification"] for audit in audits}
    for case in [load_case(record) for record in population]:
        evaluator_relation = case["perturbed_evaluator"].get("sensitivity_classification") if case["perturbed_evaluator"] else "NOT_COMPARED"
        rows.append(f"| {case['record']['candidate_id']} | {case['record']['suite']} | {case['record']['state_effect_entity']}.{case['record']['state_effect_field']} | {case['certificate']['classification']} | {evaluator_relation} | {audit_map.get(case['record']['candidate_id'], 'NOT_AUDITED')} |")
    report = f"""# Phase 2.4: R2A Held-out Task-Critical Effect Calibration

## Role and frozen population

AgentDojo remains a `CALIBRATION_TARGET`. All nine Phase 2.3 held-out candidates were frozen and attempted without selection, replacement, or return to the development set.

## Results

```json
{json.dumps(results, indent=2, sort_keys=True)}
```

## Per-case evidence

| Case | Suite | Effect | Certificate | Evaluator relation | Independent audit |
|---|---|---|---|---|---|
{chr(10).join(rows)}

## Interpretation and stop

R2A certification requires an actual wrong persistent effect witness and absence of the correct or compensating effect; argument mismatch alone is insufficient. Only certified cases entered evaluator comparison. Any transformed PASS was reproduced and source-audited under the prewritten criteria before being classified. No candidate expansion, development-set adjustment, R3 design, issue/PR search, evaluator modification, or LLM API call occurred.
"""
    (ROOT / "PHASE2_4_R2A_HELDOUT_CALIBRATION.md").write_text(report)


if __name__ == "__main__":
    main()
