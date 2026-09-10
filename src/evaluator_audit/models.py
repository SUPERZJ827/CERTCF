from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from hashlib import sha256
from typing import Any, Mapping
import json


class ApplicabilityStatus(str, Enum):
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class EvaluatorOutcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"
    UNKNOWN = "UNKNOWN"


class EvaluationMode(str, Enum):
    DIRECT = "direct"
    TRANSFORMED = "transformed"


@dataclass(frozen=True)
class Task:
    task_id: str
    prompt: str
    expected_output: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "prompt": self.prompt,
            "expected_output": self.expected_output,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CandidateSolution:
    solution_id: str
    task_id: str
    body: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        return sha256(
            json.dumps(
                {"task_id": self.task_id, "body": self.body, "metadata": self.metadata},
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()

    def with_body(self, body: str) -> "CandidateSolution":
        return replace(self, body=body)

    def to_dict(self) -> dict[str, Any]:
        return {
            "solution_id": self.solution_id,
            "task_id": self.task_id,
            "body": self.body,
            "metadata": dict(self.metadata),
            "digest": self.digest,
        }


@dataclass(frozen=True)
class ValidityEvidence:
    checks: dict[str, Any] = field(default_factory=dict)
    assumptions: tuple[str, ...] = ()
    transformed_solution_digest: str | None = None
    transformed_solution_body: str | None = None

    def with_checks(self, **items: Any) -> "ValidityEvidence":
        merged = dict(self.checks)
        merged.update(items)
        return replace(self, checks=merged)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": dict(self.checks),
            "assumptions": list(self.assumptions),
            "transformed_solution_digest": self.transformed_solution_digest,
            "transformed_solution_body": self.transformed_solution_body,
        }


@dataclass(frozen=True)
class ApplicabilityResult:
    status: ApplicabilityStatus
    reason: str | None = None
    evidence: ValidityEvidence = field(default_factory=ValidityEvidence)

    @property
    def applicable(self) -> bool:
        return self.status == ApplicabilityStatus.APPLICABLE

    @property
    def unknown(self) -> bool:
        return self.status == ApplicabilityStatus.UNKNOWN

    @property
    def not_applicable(self) -> bool:
        return self.status == ApplicabilityStatus.NOT_APPLICABLE


@dataclass(frozen=True)
class EvaluationRecordSnapshot:
    task_snapshot: str
    solution_snapshot: str
    output_snapshot: str
    exception_snapshot: str | None = None

    @classmethod
    def for_input(cls, task: Task, solution: CandidateSolution) -> "EvaluationRecordSnapshot":
        return cls(
            task_snapshot=json.dumps(task.to_dict(), sort_keys=True, ensure_ascii=False),
            solution_snapshot=json.dumps(solution.to_dict(), sort_keys=True, ensure_ascii=False),
            output_snapshot="",
        )


@dataclass(frozen=True)
class EvaluationResult:
    status: EvaluatorOutcome
    output: Any | None
    output_snapshot: str
    duration_seconds: float
    environment: dict[str, Any]
    exception: str | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    exit_status: int | None = None
    execution_config: dict[str, Any] = field(default_factory=dict)
    input_snapshot: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "output": self.output,
            "output_snapshot": self.output_snapshot,
            "duration_seconds": self.duration_seconds,
            "environment": dict(self.environment),
            "exception": self.exception,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "exit_status": self.exit_status,
            "execution_config": dict(self.execution_config),
            "input_snapshot": self.input_snapshot,
        }


@dataclass(frozen=True)
class ResultDiff:
    baseline_status: EvaluatorOutcome
    transformed_status: EvaluatorOutcome
    baseline_output: str
    transformed_output: str

    @property
    def differs(self) -> bool:
        return (
            self.baseline_status != self.transformed_status
            or self.baseline_output != self.transformed_output
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_status": self.baseline_status.value,
            "transformed_status": self.transformed_status.value,
            "baseline_output": self.baseline_output,
            "transformed_output": self.transformed_output,
            "differs": self.differs,
        }


@dataclass(frozen=True)
class ViolationRecord:
    task_id: str
    transformation_name: str
    transformed_solution_id: str
    baseline_solution_id: str
    result_diff: ResultDiff
    applicability: ApplicabilityResult
    note: str = "evaluator consistency divergence; candidate for review only"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "transformation_name": self.transformation_name,
            "transformed_solution_id": self.transformed_solution_id,
            "baseline_solution_id": self.baseline_solution_id,
            "result_diff": self.result_diff.to_dict(),
            "applicability": {
                "status": self.applicability.status.value,
                "reason": self.applicability.reason,
                "evidence": self.applicability.evidence.to_dict(),
            },
            "note": self.note,
        }
