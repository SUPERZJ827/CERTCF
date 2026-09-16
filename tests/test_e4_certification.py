from dataclasses import replace
import sqlite3
import pytest
from evaluator_audit.certification import Binding, Branch, Edit, Requirement, certify, gate_results
from evaluator_audit.adapters.appworld import read_snapshot


def fixture_case():
    initial = {"playlists": [{"id": 7, "owner": 1, "title": "old"}, {"id": 8, "owner": 1, "title": "other"}]}
    original = Branch(initial, {"playlists": [{"id": 7, "owner": 1, "title": "Road"}, {"id": 8, "owner": 1, "title": "other"}]},
                      ({"tool": "rename", "arguments": {"id": 7, "title": "Road"}},), ({"response": {"id": 7}},), True, frozenset({"playlists"}))
    changed = replace(original, final={"playlists": [{"id": 7, "owner": 1, "title": "Wrong"}, {"id": 8, "owner": 1, "title": "other"}]},
                      calls=({"tool": "rename", "arguments": {"id": 7, "title": "Wrong"}},))
    req = Requirement("Rename playlist 7 to Road", "to Road", Binding("playlists", {"id": 7, "owner": 1}, instruction_entity_span="playlist 7"), "title", "Road", grounding_rule="explicit-id-and-title-v1")
    return req, original, changed, Edit(0, "rename", "title", "Road", "Wrong")


def test_specific_entity_violation_certifies():
    assert certify(*fixture_case())["status"] == "certified"


def test_same_value_on_different_entity_does_not_restore_target():
    req, original, changed, edit = fixture_case()
    changed = replace(changed, final={"playlists": [{"id": 7, "owner": 1, "title": "Wrong"}, {"id": 8, "owner": 1, "title": "Road"}]})
    assert certify(req, original, changed, edit)["status"] == "certified"
    assert certify(req, original, changed, edit, mode="without_entity")["status"] == "rejected"


def test_ignored_change_or_compensation_cannot_be_certified():
    req, original, changed, edit = fixture_case()
    changed = replace(changed, final=original.final, immediate={0: changed.final})
    assert certify(req, original, changed, edit)["status"] == "rejected"
    assert certify(req, original, changed, edit, mode="without_temporal")["status"] == "certified"


def test_initially_correct_effect_left_intact_is_not_violation():
    req, original, changed, edit = fixture_case()
    original = replace(original, initial=original.final)
    changed = replace(changed, initial=original.initial, final=original.final)
    assert certify(req, original, changed, edit)["status"] == "rejected"


def test_dynamic_ids_bind_through_each_branch_producer_response():
    req, original, changed, edit = fixture_case()
    binding = Binding("playlists", {"owner": 1}, response_identity_field="id", response_path=("id",), producer_index=0, producer_tool="rename", instruction_entity_span="playlist 7")
    req = replace(req, binding=binding)
    changed = replace(changed, responses=({"response": {"id": 19}},), final={"playlists": [{"id": 19, "owner": 1, "title": "Wrong"}]})
    result = certify(req, original, changed, edit)
    assert result["status"] == "certified"
    assert result["counterfactual_selector"]["id"] == 19


def test_missing_response_identity_is_unknown():
    req, original, changed, edit = fixture_case()
    req = replace(req, binding=replace(req.binding, selector={"owner": 1}, response_identity_field="id", response_path=("id",), producer_index=0, producer_tool="rename"))
    changed = replace(changed, responses=({"response": {}},))
    assert certify(req, original, changed, edit)["status"] == "unknown"


def test_missing_collection_is_unknown_not_missing_effect():
    req, original, changed, edit = fixture_case()
    changed = replace(changed, final={}, complete_collections=frozenset())
    assert certify(req, original, changed, edit)["status"] == "unknown"


def test_ambiguous_selector_is_unknown():
    req, original, changed, edit = fixture_case()
    req = replace(req, binding=replace(req.binding, selector={"owner": 1}))
    assert certify(req, original, changed, edit)["status"] == "unknown"


def test_non_target_changes_are_rejected():
    req, original, changed, edit = fixture_case()
    changed = replace(changed, calls=({"tool": "rename", "arguments": {"id": 8, "title": "Wrong"}},))
    assert certify(req, original, changed, edit)["status"] == "rejected"


def test_action_obligations_remain_outside_effect_certificate():
    req, original, changed, edit = fixture_case()
    assert certify(replace(req, obligation="action"), original, changed, edit)["status"] == "unknown"


def test_relation_endpoints_are_part_of_entity_selector():
    req, original, changed, edit = fixture_case()
    req = replace(req, binding=replace(req.binding, selector={"id": 7, "owner": 2}))
    assert certify(req, original, changed, edit)["reason"] == "original_requirement_not_established"


def test_other_requirement_failure_prevents_isolated_claim():
    req, original, changed, edit = fixture_case()
    other = replace(req, binding=replace(req.binding, selector={"id": 8, "owner": 1}), expected="other")
    changed = replace(changed, final={"playlists": [{"id": 7, "owner": 1, "title": "Wrong"}, {"id": 8, "owner": 1, "title": "damaged"}]})
    assert certify(req, original, changed, edit, other_requirements=(other,))["reason"] == "other_requirement_not_preserved"


def test_simple_effect_can_retain_ignored_task_effect_edit():
    req, original, changed, edit = fixture_case()
    changed = replace(changed, final=original.final, responses=({"response": {"id": 7, "notice": "ignored"}},))
    gates = gate_results(req, original, changed, edit)
    assert gates["G2"] and not gates["G3"]


def test_sqlite_adapter_reads_records_and_refuses_missing_input(tmp_path):
    with sqlite3.connect(tmp_path / "spotify.db") as db:
        db.execute("CREATE TABLE playlists (id INTEGER, title TEXT)")
        db.execute("INSERT INTO playlists VALUES (7, 'Road')")
    assert read_snapshot(tmp_path, ("spotify.playlists",))["spotify.playlists"] == [{"id": 7, "title": "Road"}]
    with pytest.raises(FileNotFoundError):
        read_snapshot(tmp_path, ("gmail.emails",))


def test_partial_exports_cannot_hide_initial_environment_difference():
    req, original, changed, edit = fixture_case()
    original = replace(original, initial_environment_digest="snapshot-a")
    changed = replace(changed, initial_environment_digest="snapshot-b")
    assert certify(req, original, changed, edit)["checks"]["initial_equal"] is False
    changed = replace(changed, initial_environment_digest=None)
    assert certify(req, original, changed, edit)["status"] == "rejected"


def test_thinkingbox_natural_and_composite_keys_are_supported():
    from evaluator_audit.adapters.thinkingbox import branch_from_state
    state = {"inventory": [{"sku": "A", "warehouse": "B", "quantity": 1}]}
    branch = branch_from_state(state, state, [], [], success=True)
    from evaluator_audit.certification.entity_binding import resolve
    row, status, _ = resolve(Binding("inventory", {"sku": "A", "warehouse": "B"}), branch, branch.final)
    assert status == "bound" and row["quantity"] == 1


def test_relation_requires_both_endpoints_not_another_membership():
    req, original, changed, edit = fixture_case()
    req = replace(req, binding=Binding("memberships", {"user": 1, "channel": 2}, instruction_entity_span="playlist 7"), predicate="exists")
    original = replace(original, final={"memberships": [{"user": 1, "channel": 2}]}, complete_collections=frozenset({"memberships"}))
    changed = replace(changed, final={"memberships": [{"user": 1, "channel": 3}]}, complete_collections=original.complete_collections)
    assert certify(req, original, changed, edit)["status"] == "certified"


def test_numeric_boundary_is_not_silent_string_normalization():
    from evaluator_audit.certification.predicates import satisfies
    req, *_ = fixture_case()
    req = replace(req, expected=10, predicate="greater_equal")
    assert satisfies(req, {"title": 9}, "bound") is False
    assert satisfies(req, {"title": 10}, "bound") is True
    assert satisfies(req, {"title": "10"}, "bound") is None


def test_omission_requires_exact_removal_and_other_effects_preserved():
    req, original, changed, _ = fixture_case()
    changed = replace(changed, calls=(), responses=())
    edit = Edit(0, "rename", "", None, None, kind="omission")
    other = replace(req, binding=replace(req.binding, selector={"id": 8, "owner": 1}), expected="other")
    assert certify(req, original, changed, edit)["status"] == "unknown"
    assert certify(req, original, changed, edit, other_requirements=(other,))["status"] == "certified"
    changed = replace(changed, final=original.final)
    assert certify(req, original, changed, edit, other_requirements=(other,))["status"] == "rejected"


def test_scoped_existence_accepts_alternative_producer_as_completion():
    req, original, changed, edit = fixture_case()
    req = replace(req, quantifier="exists_in_scope", binding=replace(req.binding, selector={"owner": 1}))
    changed = replace(changed, final={"playlists": [{"id": 7, "owner": 1, "title": "Wrong"}, {"id": 19, "owner": 1, "title": "Road"}]})
    assert certify(req, original, changed, edit)["status"] == "rejected"


def test_scoped_existence_does_not_use_another_owners_record():
    req, original, changed, edit = fixture_case()
    req = replace(req, quantifier="exists_in_scope", binding=replace(req.binding, selector={"owner": 1}))
    changed = replace(changed, final={"playlists": [{"id": 7, "owner": 1, "title": "Wrong"}, {"id": 19, "owner": 2, "title": "Road"}]})
    assert certify(req, original, changed, edit)["status"] == "certified"


def test_scope_missing_predicate_field_is_unknown():
    req, original, changed, edit = fixture_case()
    req = replace(req, quantifier="exists_in_scope", binding=replace(req.binding, selector={"owner": 1}))
    changed = replace(changed, final={"playlists": [{"id": 7, "owner": 1}]})
    assert certify(req, original, changed, edit)["status"] == "unknown"


def test_identity_does_not_equate_boolean_with_integer():
    req, original, changed, edit = fixture_case()
    req = replace(req, binding=replace(req.binding, selector={"owner": True}))
    assert certify(req, original, changed, edit)["status"] != "certified"


def test_tool_returned_failure_is_not_successful_execution():
    req, original, changed, edit = fixture_case()
    changed = replace(changed, responses=({"response": {"success": False}},))
    assert certify(req, original, changed, edit)["status"] == "rejected"


def test_missing_call_response_cannot_certify():
    req, original, changed, edit = fixture_case()
    original = replace(original, responses=())
    assert certify(req, original, changed, edit)["checks"]["trace_complete"] is False


def test_grounding_citation_and_call_anchor_are_checked():
    from evaluator_audit.certification.grounding import validate_grounding
    req, original, *_ = fixture_case()
    req = replace(req, requirement_span="Road")
    declaration = {"citations": [{"tool": "rename", "quote": "row.title = request.title", "source_sha256": "abc"}],
                   "anchors": [{"call_index": 0, "argument": "title", "value": "Road", "span": "Road"}]}
    semantics = {"rename": {"implementation": "row.title = request.title", "source_sha256": "abc"}}
    assert validate_grounding(req, original, declaration, semantics)["status"] == "supported_declaration"
    declaration["citations"][0]["source_sha256"] = "wrong"
    assert validate_grounding(req, original, declaration, semantics)["status"] == "unknown"


def test_derived_ids_require_actual_per_branch_producer_consumer_link():
    from evaluator_audit.certification.requirements import DerivedUse
    from evaluator_audit.certification.certify import exact_edit
    req, original, changed, edit = fixture_case()
    original = replace(original, calls=original.calls + ({"tool": "consume", "arguments": {"object_id": 7}},), responses=original.responses + ({"response": {}},))
    changed = replace(changed, calls=changed.calls + ({"tool": "consume", "arguments": {"object_id": 19}},), responses=({"response": {"id": 19}}, {"response": {}}))
    assert not exact_edit(original, changed, edit)
    edit = replace(edit, kind="argument_with_derived_ids", derived_uses=(DerivedUse(0, ("id",), 1, "object_id"),))
    assert exact_edit(original, changed, edit)
    changed = replace(changed, calls=changed.calls[:1] + ({"tool": "consume", "arguments": {"object_id": 20}},))
    assert not exact_edit(original, changed, edit)


def test_nested_non_target_boolean_integer_change_is_not_hidden():
    from evaluator_audit.certification.certify import exact_edit
    req, original, changed, edit = fixture_case()
    original = replace(original, calls=({"tool": "rename", "arguments": {"id": 7, "title": "Road", "metadata": {"flag": True}}},))
    changed = replace(changed, calls=({"tool": "rename", "arguments": {"id": 7, "title": "Wrong", "metadata": {"flag": 1}}},))
    assert not exact_edit(original, changed, edit)
