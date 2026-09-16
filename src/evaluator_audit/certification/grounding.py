"""Check the provenance and anchors of an explicit grounding declaration.

This checks evidence consistency, not general natural-language entailment.
The interpretation of the cited tool semantics remains an auditable assumption.
"""
from __future__ import annotations


def validate_grounding(requirement, branch, declaration, tool_semantics):
    checks = {}
    span = requirement.requirement_span
    checks["requirement_span_present"] = bool(span) and span in requirement.instruction
    checks["entity_span_present"] = bool(requirement.binding.instruction_entity_span) and requirement.binding.instruction_entity_span in requirement.instruction
    citations = declaration.get("citations", [])
    checks["tool_source_cited"] = bool(citations)
    for i, citation in enumerate(citations):
        semantics = tool_semantics.get(citation.get("tool"), {})
        quote = citation.get("quote", "")
        checks[f"citation_{i}_exact"] = bool(quote) and quote in semantics.get("implementation", "")
        checks[f"citation_{i}_hash"] = bool(citation.get("source_sha256")) and citation.get("source_sha256") == semantics.get("source_sha256")
    anchors = declaration.get("anchors", [])
    checks["instruction_call_anchors_present"] = bool(anchors)
    for i, anchor in enumerate(anchors):
        index, argument = anchor["call_index"], anchor["argument"]
        value = branch.calls[index].get("arguments", {}).get(argument) if 0 <= index < len(branch.calls) else None
        checks[f"anchor_{i}_request"] = type(value) is type(anchor["value"]) and value == anchor["value"]
        checks[f"anchor_{i}_instruction"] = bool(anchor["span"]) and anchor["span"] in requirement.instruction
        checks[f"anchor_{i}_literal"] = str(value).casefold() == anchor["span"].casefold()
    # An alias must cite the documented human-facing term and retain its exact
    # declared normalized value. No automatic arbitrary underscore rewriting.
    alias = declaration.get("expected_alias")
    if alias:
        semantics = tool_semantics.get(alias["tool"], {})
        checks["alias_value"] = alias["value"] == requirement.expected
        checks["alias_instruction"] = alias["span"] == span
        checks["alias_documented_term"] = span.casefold() in semantics.get("description", "").casefold()
        schema = semantics.get("input_schema", {})
        field_schema = schema.get("properties", {}).get(requirement.field, {})
        if "$ref" in field_schema and field_schema["$ref"].startswith("#/$defs/"):
            field_schema = schema.get("$defs", {}).get(field_schema["$ref"].split("/")[-1], {})
        checks["alias_enum_available"] = requirement.expected in field_schema.get("enum", [])
    elif requirement.predicate != "exists":
        checks["expected_literal"] = str(requirement.expected).casefold() == span.casefold()
    return {"status": "supported_declaration" if all(checks.values()) else "unknown", "checks": checks,
            "assumption": "Cited instruction-to-tool/state interpretation is developer-declared, not independent semantic ground truth."}
