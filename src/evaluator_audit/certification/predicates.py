from __future__ import annotations
from .requirements import Requirement, typed_equal


def evaluate_requirement(requirement, branch, state):
    from .entity_binding import resolve, selector_for
    if requirement.quantifier == "unique":
        row, reason, selector = resolve(requirement.binding, branch, state)
        return satisfies(requirement, row, reason), reason, selector
    if requirement.quantifier != "exists_in_scope":
        return None, "unsupported_quantifier", None
    selector, reason = selector_for(requirement.binding, branch)
    if selector is None:
        return None, reason, None
    collection = requirement.binding.collection
    if collection not in branch.complete_collections or collection not in state:
        return None, "collection_unobservable", selector
    rows = state[collection]
    if any(not isinstance(row, dict) for row in rows):
        return None, "malformed_collection", selector
    matched = [row for row in rows if all(k in row and type(row[k]) is type(v) and row[k] == v for k, v in selector.items())]
    values = [satisfies(requirement, row, "bound") for row in matched]
    if any(v is True for v in values):
        return True, "scoped_witness", selector
    if any(v is None for v in values):
        return None, "scope_predicate_unobservable", selector
    return False, "no_satisfying_scoped_entity", selector


def satisfies(requirement: Requirement, row: dict | None, resolution: str) -> bool | None:
    if resolution == "entity_absent":
        return False
    if resolution == "bound" and requirement.predicate == "exists":
        return True
    if resolution != "bound" or row is None or requirement.field not in row:
        return None
    value = row[requirement.field]
    if requirement.predicate == "equals":
        # Never silently normalize roles, amounts, strings, or identifiers.
        return typed_equal(value, requirement.expected)
    if requirement.predicate == "contains":
        return any(typed_equal(requirement.expected, item) for item in value) if isinstance(value, (list, tuple, set)) else None
    if requirement.predicate in ("greater_equal", "less_equal"):
        if type(value) not in (int, float) or type(requirement.expected) not in (int, float):
            return None
        return value >= requirement.expected if requirement.predicate == "greater_equal" else value <= requirement.expected
    return None
