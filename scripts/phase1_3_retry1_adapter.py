"""Representation-only persisted FunctionCall adapter for Phase 1.3 retry1."""

from __future__ import annotations

from typing import Any, Mapping

from agentdojo.functions_runtime import FunctionCall


def persisted_call_to_runtime(record: Mapping[str, Any]) -> FunctionCall:
    """Map persisted `arguments` to runtime `args` without changing values."""
    payload = dict(record)
    if "arguments" not in payload or "args" in payload:
        raise ValueError("Expected exactly the persisted 'arguments' field")
    payload["args"] = payload.pop("arguments")
    return FunctionCall.model_validate(payload)
