from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from .models import (
    ApplicabilityResult,
    EvaluationResult,
    ViolationRecord,
    Task,
    CandidateSolution,
    EvaluatorOutcome,
)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class AuditRecord:
    run_id: str
    task: Task
    baseline_solution: CandidateSolution
    transformation_name: str
    applicability: ApplicabilityResult
    baseline_result: EvaluationResult | None
    transformed_solution: CandidateSolution | None
    transformed_result: EvaluationResult | None
    violations: list[ViolationRecord] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "task": self.task.to_dict(),
            "baseline_solution": self.baseline_solution.to_dict(),
            "transformation": self.transformation_name,
            "applicability": {
                "status": self.applicability.status.value,
                "reason": self.applicability.reason,
                "evidence": self.applicability.evidence.to_dict(),
            },
            "baseline_result": self.baseline_result.to_dict() if self.baseline_result else None,
            "transformed_solution": self.transformed_solution.to_dict() if self.transformed_solution else None,
            "transformed_result": self.transformed_result.to_dict() if self.transformed_result else None,
            "violations": [violation.to_dict() for violation in self.violations],
            "record_summary": {
                "status": self.overall_status.value,
                "violations": len(self.violations),
                "has_timeout": any(
                    (result and result.status == EvaluatorOutcome.TIMEOUT)
                    for result in (self.baseline_result, self.transformed_result)
                    if result is not None
                ),
                "has_error": any(
                    (result and result.status == EvaluatorOutcome.ERROR)
                    for result in (self.baseline_result, self.transformed_result)
                    if result is not None
                ),
            },
        }

    @property
    def overall_status(self) -> EvaluatorOutcome:
        if self.applicability.status.name == "UNKNOWN":
            return EvaluatorOutcome.UNKNOWN
        if self.applicability.status.name == "NOT_APPLICABLE":
            return EvaluatorOutcome.UNKNOWN
        if not self.transformed_result:
            return EvaluatorOutcome.UNKNOWN
        if self.violations:
            return EvaluatorOutcome.UNKNOWN
        if self.transformed_result.status == EvaluatorOutcome.PASS:
            return EvaluatorOutcome.PASS if self.baseline_result and self.baseline_result.status == EvaluatorOutcome.PASS else EvaluatorOutcome.FAIL
        return self.transformed_result.status

    @property
    def integrity_fingerprint(self) -> str:
        payload = {
            "run_id": self.run_id,
            "task_id": self.task.task_id,
            "baseline_solution": self.baseline_solution.digest,
            "transformation": self.transformation_name,
            "applicability": self.applicability.status.value,
            "baseline_result": self.baseline_result.to_dict() if self.baseline_result else None,
            "transformed_result": self.transformed_result.to_dict() if self.transformed_result else None,
        }
        return sha256(
            repr(payload).encode("utf-8")
        ).hexdigest()
