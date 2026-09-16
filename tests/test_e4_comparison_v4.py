from dataclasses import replace

from evaluator_audit.certification.requirements import Binding, Requirement, Branch, Edit
from evaluator_audit.certification.comparison_v4 import compare, summarize, METHODS


def example():
    req = Requirement("Rename item A to new title", "new title", Binding("items", {"id": "A"}, instruction_entity_span="A"),
                      "title", "new title", grounding_rule="declared")
    a = Branch({"items": []}, {"items": [{"id": "A", "title": "new title"}]},
               ({"tool": "rename", "arguments": {"title": "new title"}},),
               ({"response": {"success": True}},), True, frozenset({"items"}))
    b = replace(a, calls=({"tool": "rename", "arguments": {"title": "wrong"}},),
                final={"items": [{"id": "A", "title": "wrong"}]},
                immediate={0: {"items": [{"id": "A", "title": "wrong"}]}})
    return req, a, b, Edit(0, "rename", "title", "new title", "wrong")


def test_grounding_unknown_applies_to_every_method_but_certificate_runs():
    result = compare(*example(), {"status": "unknown"})
    assert all(v["status"] == "unknown" for v in result["methods"].values())
    assert result["raw_certificate"]["status"] == "certified"


def test_entity_ablation_removes_only_alternative_binding():
    req, a, b, edit = example()
    b = replace(b, final={"items": [*b.final["items"], {"id": "B", "title": "new title"}]})
    result = compare(req, a, b, edit, {"status": "supported_declaration"})["methods"]
    assert result["G3"]["status"] == "certified"
    assert result["G3_without_entity"]["status"] == "rejected"
    assert result["G2b"]["status"] == "rejected"


def test_entity_ablation_no_witness_is_defined():
    result = compare(*example(), {"status": "supported_declaration"})["methods"]
    assert result["G3_without_entity"]["status"] == "certified"


def test_temporal_compensation():
    req, a, b, edit = example()
    b = replace(b, final=a.final)
    result = compare(req, a, b, edit, {"status": "supported_declaration"})["methods"]
    assert result["G3"]["status"] == "rejected"
    assert result["G3_without_temporal"]["status"] == "certified"


def test_relation_ablation_is_unknown_not_rejected():
    req, a, b, edit = example()
    req = replace(req, predicate="exists")
    result = compare(req, a, b, edit, {"status": "supported_declaration"})["methods"]
    assert result["G2b"]["status"] == result["G3_without_entity"]["status"] == "unknown"


def test_alias_not_retested_as_raw_literal():
    req, a, b, edit = example()
    req = replace(req, instruction="Rename item A to human alias", requirement_span="human alias")
    result = compare(req, a, b, edit, {"status": "supported_declaration"})["methods"]
    assert result["G0"]["status"] == "certified"


def test_unknown_has_separate_denominator():
    rows = [{"comparison": compare(*example(), {"status": "unknown"}),
             "label": {"alternative_label": "REQUIREMENT_VIOLATED"}}]
    result = summarize(rows)
    for method in METHODS:
        assert result[method]["unknown"] == 1
        assert result[method]["rejected"] == result[method]["defined"] == 0


def test_missing_field_with_positive_witness_still_rejects_absence():
    req, a, b, edit = example()
    b = replace(b, final={"items": [{"id": "B", "title": "new title"}, {"id": "C"}]})
    result = compare(req, a, b, edit, {"status": "supported_declaration"})["methods"]
    assert result["G2b"]["status"] == "rejected"
