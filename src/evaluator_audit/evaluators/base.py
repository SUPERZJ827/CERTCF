from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Callable, Mapping, Protocol
import platform
import sys
import time

from ..models import (
    EvaluationRecordSnapshot,
    EvaluationResult,
    EvaluatorOutcome,
    Task,
    CandidateSolution,
)


class EvaluatorAdapter(Protocol):
    def run(
        self, task: Task, solution: CandidateSolution, config: "ExecutionConfig"
    ) -> EvaluationResult:
        ...


EvaluatorFn = Callable[[Task, CandidateSolution], Any]


@dataclass(frozen=True)
class ExecutionConfig:
    timeout_seconds: float = 1.0
    extra_env: Mapping[str, str] = field(default_factory=dict)


def _snapshot_env(extra_env: Mapping[str, str]) -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "python": sys.version,
        "extra_env_keys": sorted(extra_env),
    }


def _normalize_output(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return repr(value)
    if isinstance(value, Mapping):
        return sha256(
            str(sorted(value.items())).encode("utf-8")
        ).hexdigest()
    return value.__class__.__name__


def _infer_outcome(value: Any) -> EvaluatorOutcome:
    if isinstance(value, EvaluatorOutcome):
        return value
    if isinstance(value, bool):
        return EvaluatorOutcome.PASS if value else EvaluatorOutcome.FAIL
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"pass", "accepted", "true", "1", "ok"}:
            return EvaluatorOutcome.PASS
        if text in {"fail", "rejected", "false", "0", "bad"}:
            return EvaluatorOutcome.FAIL
    if value is None:
        return EvaluatorOutcome.UNKNOWN
    return EvaluatorOutcome.FAIL


def _invoke(task: Task, solution: CandidateSolution, fn: EvaluatorFn) -> Any:
    return fn(task, solution)


@dataclass
class CallableEvaluatorAdapter:
    fn: EvaluatorFn

    def run(
        self, task: Task, solution: CandidateSolution, config: ExecutionConfig
    ) -> EvaluationResult:
        timeout = max(float(config.timeout_seconds), 0.001)
        start = time.perf_counter()
        input_snapshot = EvaluationRecordSnapshot.for_input(task, solution)
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_invoke, task, solution, self.fn)
                value = future.result(timeout=timeout)
        except FutureTimeoutError:
            end = time.perf_counter()
            return EvaluationResult(
                status=EvaluatorOutcome.TIMEOUT,
                output=None,
                output_snapshot="",
                exception=f"timeout after {timeout} seconds",
                duration_seconds=round(end - start, 6),
                environment=_snapshot_env(config.extra_env),
                timed_out=True,
                execution_config={"timeout_seconds": timeout, "extra_env": dict(config.extra_env)},
                input_snapshot=input_snapshot.task_snapshot + "\n" + input_snapshot.solution_snapshot,
            )
        except BaseException as exc:  # broad on purpose to preserve evaluator errors as evidence
            end = time.perf_counter()
            return EvaluationResult(
                status=EvaluatorOutcome.ERROR,
                output=None,
                output_snapshot="",
                exception=f"{type(exc).__name__}: {exc}",
                duration_seconds=round(end - start, 6),
                environment=_snapshot_env(config.extra_env),
                execution_config={"timeout_seconds": timeout, "extra_env": dict(config.extra_env)},
                input_snapshot=input_snapshot.task_snapshot + "\n" + input_snapshot.solution_snapshot,
            )
        else:
            end = time.perf_counter()
            outcome = _infer_outcome(value)
            return EvaluationResult(
                status=outcome,
                output=value,
                output_snapshot=_normalize_output(value),
                duration_seconds=round(end - start, 6),
                environment=_snapshot_env(config.extra_env),
                execution_config={"timeout_seconds": timeout, "extra_env": dict(config.extra_env)},
                input_snapshot=input_snapshot.task_snapshot + "\n" + input_snapshot.solution_snapshot,
            )
