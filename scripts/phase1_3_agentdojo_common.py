"""Frozen common runtime and canonicalization for AgentDojo Phase 1.3."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from agentdojo.functions_runtime import FunctionCall, FunctionCallArgTypes, FunctionReturnType, FunctionsRuntime, TaskEnvironment

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "reference" / "agentdojo"
OUT = ROOT / "artifacts" / "phase1_3"
BENCHMARK_VERSION = "v1.2.1"
COMMIT = "357c80dea9af34323f709c3505d9e6d224654c7e"


def canonical_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", round_trip=True)
    if isinstance(value, Mapping):
        return {str(key): canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported canonical value: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(canonical_value(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(canonical_value(value), ensure_ascii=True, sort_keys=True, indent=2) + "\n")


def call_data(call: FunctionCall) -> dict[str, Any]:
    return {
        "function": call.function,
        "arguments": canonical_value(call.args),
        "id": call.id,
        "placeholder_args": canonical_value(call.placeholder_args) if call.placeholder_args is not None else None,
    }


class RecordingFunctionsRuntime(FunctionsRuntime):
    """Delegates unchanged execution while recording complete raw responses."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.records: list[dict[str, Any]] = []

    def run_function(
        self,
        env: TaskEnvironment | None,
        function: str,
        kwargs: Mapping[str, FunctionCallArgTypes],
        raise_on_error: bool = False,
    ) -> tuple[FunctionReturnType, str | None]:
        try:
            result = super().run_function(env, function, kwargs, raise_on_error=raise_on_error)
            self.records.append({"sequence_number": len(self.records), "function": function, "arguments": canonical_value(dict(kwargs)), "response": canonical_value(result[0]), "error": result[1]})
            return result
        except Exception as exc:
            self.records.append({"sequence_number": len(self.records), "function": function, "arguments": canonical_value(dict(kwargs)), "response": None, "exception": {"type": type(exc).__name__, "message": str(exc)}})
            raise
