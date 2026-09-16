from __future__ import annotations
from .entity_binding import resolve
from .predicates import satisfies, evaluate_requirement
from .requirements import Branch, Edit, Requirement, typed_equal


def exact_edit(original: Branch, changed: Branch, edit: Edit) -> bool:
    if edit.kind == "omission":
        return (0 <= edit.index < len(original.calls)
                and original.calls[edit.index].get("tool") == edit.tool
                and len(original.calls) == len(changed.calls) + 1
                and typed_equal(original.calls[:edit.index] + original.calls[edit.index + 1:], changed.calls))
    if edit.kind not in ("argument", "argument_with_derived_ids") or edit.index < 0 or len(original.calls) != len(changed.calls) or edit.index >= len(original.calls):
        return False
    differences = []
    for index, (left, right) in enumerate(zip(original.calls, changed.calls)):
        if left.get("tool") != right.get("tool"):
            return False
        a, b = left.get("arguments", {}), right.get("arguments", {})
        for key in set(a) | set(b):
            if key not in a or key not in b or not typed_equal(a[key], b[key]):
                differences.append((index, key, a.get(key), b.get(key)))
    expected = [(edit.index, edit.argument, edit.original, edit.replacement)]
    if edit.kind == "argument_with_derived_ids":
        if not edit.derived_uses:
            return False
        used = set()
        for use in edit.derived_uses:
            if not (0 <= use.producer_index < use.consumer_index < len(original.calls)):
                return False
            key = (use.consumer_index, use.argument)
            if key in used or key == (edit.index, edit.argument):
                return False
            used.add(key)
            values = []
            for branch in (original, changed):
                try:
                    value = branch.responses[use.producer_index]["response"]
                    for item in use.response_path:
                        value = value[item]
                    actual = branch.calls[use.consumer_index]["arguments"][use.argument]
                except (IndexError, KeyError, TypeError):
                    return False
                if type(value) not in (str, int) or type(actual) is not type(value) or actual != value:
                    return False
                values.append(value)
            if type(values[0]) is not type(values[1]) or values[0] != values[1]:
                expected.append((use.consumer_index, use.argument, *values))
    elif edit.derived_uses:
        return False
    return (original.calls[edit.index].get("tool") == edit.tool and edit.original != edit.replacement
            and sorted(differences, key=lambda x: x[:2]) == sorted(expected, key=lambda x: x[:2]))


def execution_checks(original: Branch, changed: Branch, edit: Edit) -> dict:
    def successful(response):
        raw = response.get("response")
        return not response.get("exception") and not response.get("error") and not (isinstance(raw, dict) and raw.get("success") is False)
    target_success = (0 <= edit.index < len(changed.responses)
                      and successful(changed.responses[edit.index]))
    if edit.kind == "omission":
        target_success = exact_edit(original, changed, edit) and changed.success
    initial_equal = typed_equal(original.initial, changed.initial)
    if original.initial_environment_digest is not None or changed.initial_environment_digest is not None:
        initial_equal = initial_equal and bool(original.initial_environment_digest) and original.initial_environment_digest == changed.initial_environment_digest
    return {"exact_edit": exact_edit(original, changed, edit), "initial_equal": initial_equal,
            "both_executions_successful": original.success and changed.success,
            "trace_complete": len(original.calls) == len(original.responses) and len(changed.calls) == len(changed.responses),
            "all_recorded_calls_successful": all(successful(r) for r in (*original.responses, *changed.responses)),
            "target_success": target_success}


def certify(requirement: Requirement, original: Branch, changed: Branch, edit: Edit, *, mode: str = "full", other_requirements: tuple[Requirement, ...] = ()) -> dict:
    if mode not in ("full", "without_entity", "without_temporal"):
        raise ValueError("Unknown certificate mode")
    checks = execution_checks(original, changed, edit)
    if not all(checks.values()):
        return {"status": "rejected", "reason": "execution_or_exactness_failed", "checks": checks}
    if requirement.obligation != "final_effect":
        return {"status": "unknown", "reason": "action_obligation_outside_effect_scope", "checks": checks}
    if edit.kind == "omission" and not other_requirements:
        return {"status": "unknown", "reason": "omission_isolation_requirements_missing", "checks": checks}
    grounded = (bool(requirement.requirement_span) and requirement.requirement_span in requirement.instruction
                and bool(requirement.binding.instruction_entity_span)
                and requirement.binding.instruction_entity_span in requirement.instruction and bool(requirement.grounding_rule))
    if not grounded:
        return {"status": "unknown", "reason": "instruction_grounding_missing", "checks": checks}
    initial_validity, resolution, selector = evaluate_requirement(requirement, original, original.final)
    checks["original_requirement_satisfied"] = initial_validity
    if initial_validity is not True:
        return {"status": "unknown" if initial_validity is None else "rejected", "reason": "original_requirement_not_established", "checks": checks}
    state = changed.final
    if mode == "without_temporal":
        state = changed.immediate.get(edit.index)
        if state is None:
            return {"status": "unknown", "reason": "immediate_snapshot_missing", "checks": checks}
    if mode == "without_entity":
        if requirement.predicate != "equals" or edit.kind not in ("argument", "argument_with_derived_ids"):
            return {"status": "unknown", "reason": "field_proxy_not_defined_for_relation_or_omission", "checks": checks}
        collection = requirement.binding.collection
        if collection not in changed.complete_collections or collection not in state:
            return {"status": "unknown", "reason": "collection_unobservable", "checks": checks}
        values = [r[requirement.field] for r in state[collection] if requirement.field in r]
        # Field-value proxy deliberately lacks entity identity: it uses presence
        # of the wrong value and global absence of the expected value.
        violated = edit.replacement in values and requirement.expected not in values
        target_resolution = "unbound_field_proxy"
        target_selector = None
    else:
        sat, target_resolution, target_selector = evaluate_requirement(requirement, changed, state)
        if sat is None:
            return {"status": "unknown", "reason": target_resolution if target_resolution != "bound" else "field_or_predicate_unobservable", "checks": checks}
        violated = not sat
    checks["target_requirement_violated"] = violated
    for index, other in enumerate(other_requirements):
        baseline_ok, _, _ = evaluate_requirement(other, original, original.final)
        checks[f"other_requirement_{index}_original_satisfied"] = baseline_ok
        if baseline_ok is not True:
            return {"status": "unknown" if baseline_ok is None else "rejected", "reason": "other_original_requirement_not_established", "checks": checks}
        ok, _, _ = evaluate_requirement(other, changed, changed.final)
        checks[f"other_requirement_{index}_preserved"] = ok
        if ok is not True:
            return {"status": "unknown" if ok is None else "rejected", "reason": "other_requirement_not_preserved", "checks": checks}
    return {"status": "certified" if violated else "rejected", "reason": "target_requirement_violated" if violated else "target_requirement_satisfied_or_compensated",
            "checks": checks, "original_selector": selector, "counterfactual_selector": target_selector,
            "binding_resolution": target_resolution,
            "assumptions": ["declared instruction-to-entity mapping is correct", "adapter exports complete declared collections"],
            "mode": mode}


def gate_results(requirement: Requirement, original: Branch, changed: Branch, edit: Edit, *, other_requirements: tuple[Requirement, ...] = ()) -> dict:
    checks = execution_checks(original, changed, edit)
    literal = isinstance(edit.original, (str, int, float)) and str(edit.original) in requirement.instruction
    if edit.kind == "omission":
        literal = bool(requirement.requirement_span) and requirement.requirement_span in requirement.instruction
    g0 = bool(checks["exact_edit"] and literal)
    g1 = bool(g0 and all(checks.values()))
    g2 = bool(g1 and (not typed_equal(original.final, changed.final) or not typed_equal(original.responses, changed.responses)))
    collection = requirement.binding.collection
    before = [r.get(requirement.field) for r in original.final.get(collection, [])]
    after = [r.get(requirement.field) for r in changed.final.get(collection, [])]
    g2b = bool(g1 and not typed_equal(before, after) and any(typed_equal(edit.replacement, value) for value in after))
    full = certify(requirement, original, changed, edit, other_requirements=other_requirements)
    no_entity = certify(requirement, original, changed, edit, mode="without_entity", other_requirements=other_requirements)
    no_time = certify(requirement, original, changed, edit, mode="without_temporal", other_requirements=other_requirements)
    return {"G0": g0, "G1": g1, "G2": g2, "G2b": g2b,
            "G3": full["status"] == "certified", "G3_without_entity": no_entity["status"] == "certified",
            "G3_without_temporal": no_time["status"] == "certified", "certificate": full,
            "ablation_certificates": {"without_entity": no_entity, "without_temporal": no_time}}
