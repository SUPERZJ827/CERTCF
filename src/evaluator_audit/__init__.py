"""Minimal evaluator-audit research package for Phase 0."""

from .models import (
    ApplicabilityResult,
    ApplicabilityStatus,
    CandidateSolution,
    EvaluationMode,
    EvaluationRecordSnapshot,
    EvaluationResult,
    EvaluatorOutcome,
    ResultDiff,
    Task,
    ValidityEvidence,
    ViolationRecord,
)
from .runner import run_audit_cycle
from .records import AuditRecord

__all__ = [
    "ApplicabilityResult",
    "ApplicabilityStatus",
    "CandidateSolution",
    "EvaluationMode",
    "EvaluationRecordSnapshot",
    "EvaluationResult",
    "EvaluatorOutcome",
    "ResultDiff",
    "Task",
    "ValidityEvidence",
    "ViolationRecord",
    "AuditRecord",
    "run_audit_cycle",
]
