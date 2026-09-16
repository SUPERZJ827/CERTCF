from __future__ import annotations
from typing import Any, Mapping
from .requirements import Binding, Branch


def selector_for(binding: Binding, branch: Branch) -> tuple[dict | None, str]:
    selector = dict(binding.selector)
    if binding.response_identity_field is not None:
        index = binding.producer_index
        if index is None or index < 0 or index >= len(branch.responses) or index >= len(branch.calls):
            return None, "producer_response_missing"
        if not binding.producer_tool or branch.calls[index].get("tool") != binding.producer_tool:
            return None, "producer_identity_mismatch"
        value: Any = branch.responses[index].get("response")
        try:
            for key in binding.response_path:
                value = value[key]
        except (KeyError, TypeError, IndexError):
            return None, "producer_identity_unobservable"
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            return None, "producer_identity_invalid"
        if binding.response_identity_field in selector and selector[binding.response_identity_field] != value:
            return None, "conflicting_entity_binding"
        selector[binding.response_identity_field] = value
    if not selector:
        return None, "empty_entity_binding"
    return selector, "bound"


def resolve(binding: Binding, branch: Branch, state: Mapping[str, list[dict]]) -> tuple[dict | None, str, dict | None]:
    selector, reason = selector_for(binding, branch)
    if selector is None:
        return None, reason, None
    if binding.collection not in branch.complete_collections or binding.collection not in state:
        return None, "collection_unobservable", selector
    rows = state[binding.collection]
    if any(not isinstance(row, dict) for row in rows):
        return None, "malformed_collection", selector
    matches = [row for row in rows if all(key in row and type(row[key]) is type(value) and row[key] == value for key, value in selector.items())]
    if len(matches) > 1:
        return None, "ambiguous_entity", selector
    if not matches:
        return None, "entity_absent", selector
    return matches[0], "bound", selector
