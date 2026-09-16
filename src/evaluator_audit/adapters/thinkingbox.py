from __future__ import annotations
import copy
from typing import Mapping
from evaluator_audit.certification.requirements import Branch


def branch_from_state(initial: Mapping, final: Mapping, requests: list[dict], responses: list[dict], *, success: bool, immediate: Mapping | None = None) -> Branch:
    def validate(state):
        if not isinstance(state, dict) or any(not isinstance(v, list) or any(not isinstance(r, dict) for r in v) for v in state.values()):
            raise ValueError("ThinkingBox export must contain complete lists of entity records")
        # Some official tables use sku or a composite key, not a field named id.
        # Identity is checked by the declared selector in the shared resolver.
        return copy.deepcopy(state)
    a, b = validate(initial), validate(final)
    calls = tuple({"tool": x.get("tool", x.get("name")), "arguments": copy.deepcopy(x["arguments"])} for x in requests)
    return Branch(a, b, calls, tuple(copy.deepcopy(responses)), success, frozenset(a) & frozenset(b),
                  {k: validate(v) for k, v in (immediate or {}).items()})
