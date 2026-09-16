from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Mapping


def typed_equal(left, right):
    """JSON evidence equality that never identifies true with 1 recursively."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(typed_equal(left[k], right[k]) for k in left)
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(typed_equal(a, b) for a, b in zip(left, right))
    return left == right


@dataclass(frozen=True)
class Binding:
    collection: str
    selector: Mapping[str, Any]
    # Optional dynamic identity comes directly from the producer's raw response.
    # The field is added to selector separately for each execution branch.
    response_identity_field: str | None = None
    response_path: tuple[str, ...] = ()
    producer_index: int | None = None
    producer_tool: str | None = None
    instruction_entity_span: str = ""


@dataclass(frozen=True)
class Requirement:
    instruction: str
    requirement_span: str
    binding: Binding
    field: str
    expected: Any
    obligation: str = "final_effect"
    predicate: str = "equals"
    grounding_rule: str = ""
    # Explicit task semantics: unique named entity versus existence within an
    # instruction-grounded scope. Producer identity alone is not sufficient
    # for goals that can also be completed by an alternative producer.
    quantifier: str = "unique"


@dataclass(frozen=True)
class DerivedUse:
    producer_index: int
    response_path: tuple[str, ...]
    consumer_index: int
    argument: str


@dataclass(frozen=True)
class Edit:
    index: int
    tool: str
    argument: str
    original: Any
    replacement: Any
    kind: str = "argument"
    derived_uses: tuple[DerivedUse, ...] = ()


@dataclass(frozen=True)
class Branch:
    initial: Mapping[str, list[dict]]
    final: Mapping[str, list[dict]]
    calls: tuple[dict, ...]
    responses: tuple[dict, ...]
    success: bool
    complete_collections: frozenset[str]
    # Snapshots immediately after specific calls; absent snapshots remain unknown.
    immediate: Mapping[int, Mapping[str, list[dict]]] = field(default_factory=dict)
    # Required when an adapter exports only a subset of the initial environment.
    initial_environment_digest: str | None = None
