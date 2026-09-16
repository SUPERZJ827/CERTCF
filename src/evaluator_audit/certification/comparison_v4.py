"""Development comparison with shared grounding and explicit three-state outcomes.

These are admission gates for *focal violation tests*, not full-task verifiers.
Weak gates are heuristics, not certificates of natural-language correctness.
"""
from .certify import certify, execution_checks
from .requirements import typed_equal


METHODS = ("G0", "G1", "G2", "G2b", "G3", "G3_without_entity", "G3_without_temporal")


def outcome(value, reason):
    return {"status": "unknown" if value is None else "certified" if value else "rejected", "reason": reason}


def compare(requirement, original, changed, edit, grounding, *, other_requirements=()):
    checks = execution_checks(original, changed, edit)
    # Always exercise the real certificate, even for unsupported grounding.
    raw = certify(requirement, original, changed, edit, other_requirements=other_requirements)
    if grounding["status"] != "supported_declaration":
        return {"methods": {m: outcome(None, "shared_grounding_not_established") for m in METHODS},
                "raw_certificate": raw, "execution_checks": checks}
    methods = {
        "G0": outcome(checks["exact_edit"], "exact_edit_with_shared_instruction_interpretation"),
        "G1": outcome(all(checks.values()), "execution_and_exactness"),
        "G2": outcome(all(checks.values()) and (not typed_equal(original.final, changed.final)
                   or not typed_equal(original.responses, changed.responses)), "observable_state_or_response_change"),
        "G3": raw,
    }
    # Same canonical expected value and predicate; remove only entity binding.
    # Relation existence and omission proxies remain undefined, not false.
    if requirement.predicate != "equals" or edit.kind == "omission":
        methods["G2b"] = outcome(None, "unbound_field_proxy_undefined_for_relation_or_omission")
        methods["G3_without_entity"] = outcome(None, "entity_ablation_undefined_for_relation_or_omission")
    else:
        collection = requirement.binding.collection
        if not all(checks.values()):
            methods["G2b"] = outcome(False, "execution_or_exactness_failed")
        elif collection not in changed.complete_collections or collection not in changed.final:
            methods["G2b"] = outcome(None, "collection_unobservable")
        else:
            records = changed.final[collection]
            present = any(requirement.field in r and typed_equal(r[requirement.field], requirement.expected) for r in records)
            if not present and any(requirement.field not in r for r in records):
                methods["G2b"] = outcome(None, "field_unobservable")
            else:
                absent = not present
                methods["G2b"] = outcome(absent, "canonical_expected_value_absent_without_entity_binding")
        # Retain the common bound reference validity/execution/isolation checks.
        # Remove binding only from the alternative target-effect assessment.
        # Do not feed an empty selector to the core (it rightly means unknown).
        if raw["reason"] not in ("target_requirement_violated", "target_requirement_satisfied_or_compensated"):
            methods["G3_without_entity"] = dict(raw)
        else:
            methods["G3_without_entity"] = {**methods["G2b"],
                "common_reference_and_isolation_checks": "retained_from_full_certificate",
                "ablation_scope": "alternative_target_entity_binding_only"}
    methods["G3_without_temporal"] = (outcome(None, "omission_has_no_corresponding_post_target_snapshot")
        if edit.kind == "omission" else certify(requirement, original, changed, edit,
            mode="without_temporal", other_requirements=other_requirements))
    return {"methods": methods, "raw_certificate": raw, "execution_checks": checks}


def summarize(rows):
    result = {}
    for method in METHODS:
        counts = {s: sum(r["comparison"]["methods"][method]["status"] == s for r in rows)
                  for s in ("certified", "rejected", "unknown")}
        counts["total"] = len(rows)
        counts["defined"] = counts["certified"] + counts["rejected"]
        # Descriptive v3 focal labels only; never treat unknown as rejected.
        counts["v3_focal_labels_by_status"] = {
            s: {label: sum(r["comparison"]["methods"][method]["status"] == s
                          and r["label"]["alternative_label"] == label for r in rows)
                for label in ("REQUIREMENT_SATISFIED", "REQUIREMENT_VIOLATED", "AMBIGUOUS", "INSUFFICIENT_EVIDENCE")}
            for s in ("certified", "rejected", "unknown")}
        result[method] = counts
    return result
