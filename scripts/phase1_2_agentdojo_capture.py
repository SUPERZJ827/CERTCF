"""Non-invasive FunctionsRuntime recorder for Phase 1.2 original-only calibration."""

from __future__ import annotations

from typing import Any, Mapping

from agentdojo.functions_runtime import FunctionCallArgTypes, FunctionReturnType, FunctionsRuntime, TaskEnvironment


class RecordingFunctionsRuntime(FunctionsRuntime):
    """Records exact runtime inputs/results while delegating unchanged behavior to upstream."""

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
            self.records.append({"function": function, "arguments": dict(kwargs), "response": result[0], "error": result[1]})
            return result
        except Exception as exc:
            self.records.append({"function": function, "arguments": dict(kwargs), "exception": {"type": type(exc).__name__, "message": str(exc)}})
            raise
