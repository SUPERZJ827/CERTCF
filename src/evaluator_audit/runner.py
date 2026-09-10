from __future__ import annotations

import uuid

from .evaluators import EvaluatorAdapter, ExecutionConfig
from .models import (
    ApplicabilityResult,
    ApplicabilityStatus,
    CandidateSolution,
    ResultDiff,
    Task,
    ValidityEvidence,
    ViolationRecord,
    EvaluatorOutcome,
)
from .records import AuditRecord
from .transformations import BaseTransformation


def _run_once(evaluator: EvaluatorAdapter, task: Task, solution: CandidateSolution, config: ExecutionConfig) -> EvaluationResult:
    return evaluator.run(task=task, solution=solution, config=config)


def run_audit_cycle(
    task: Task,
    baseline_solution: CandidateSolution,
    transformation: BaseTransformation,
    evaluator: EvaluatorAdapter,
    config: ExecutionConfig | None = None,
) -> AuditRecord:
    execution_config = config or ExecutionConfig()
    applicability = transformation.check_applicability(task=task, solution=baseline_solution)
    if applicability.status == ApplicabilityStatus.UNKNOWN:
        return AuditRecord(
            run_id=str(uuid.uuid4()),
            task=task,
            baseline_solution=baseline_solution,
            transformation_name=transformation.name,
            applicability=applicability,
            baseline_result=None,
            transformed_solution=None,
            transformed_result=None,
            violations=[],
        )

    if applicability.status == ApplicabilityStatus.NOT_APPLICABLE:
        return AuditRecord(
            run_id=str(uuid.uuid4()),
            task=task,
            baseline_solution=baseline_solution,
            transformation_name=transformation.name,
            applicability=applicability,
            baseline_result=None,
            transformed_solution=None,
            transformed_result=None,
            violations=[],
        )

    transformed_solution = transformation.apply(task=task, solution=baseline_solution, applicability=applicability)
    transformed_solution = CandidateSolution(
        solution_id=f"{transformed_solution.solution_id}:{transformation.name}",
        task_id=transformed_solution.task_id,
        body=transformed_solution.body,
        metadata=dict(transformed_solution.metadata),
    )
    base_evidence = applicability.evidence.with_checks(
        transformation=transformation.name,
        transformed_solution_digest=transformed_solution.digest,
        transformed_solution_body=transformed_solution.body,
    )
    applicability = ApplicabilityResult(
        status=applicability.status,
        reason=applicability.reason,
        evidence=base_evidence,
    )

    baseline_result = _run_once(evaluator, task, baseline_solution, execution_config)
    transformed_result = _run_once(evaluator, task, transformed_solution, execution_config)

    violations: list[ViolationRecord] = []
    if baseline_result.status == EvaluatorOutcome.UNKNOWN:
        # Can't infer correctness when baseline cannot be classified.
        pass
    elif transformed_result.status == EvaluatorOutcome.UNKNOWN:
        pass
    else:
        diff = ResultDiff(
            baseline_status=baseline_result.status,
            transformed_status=transformed_result.status,
            baseline_output=str(baseline_result.output_snapshot),
            transformed_output=str(transformed_result.output_snapshot),
        )
        if diff.differs:
            violations.append(
                ViolationRecord(
                    task_id=task.task_id,
                    transformation_name=transformation.name,
                    transformed_solution_id=transformed_solution.solution_id,
                    baseline_solution_id=baseline_solution.solution_id,
                    result_diff=diff,
                    applicability=applicability,
                    note="evaluator consistency divergence; candidate for review only",
                )
            )

    return AuditRecord(
        run_id=str(uuid.uuid4()),
        task=task,
        baseline_solution=baseline_solution,
        transformation_name=transformation.name,
        applicability=applicability,
        baseline_result=baseline_result,
        transformed_solution=transformed_solution,
        transformed_result=transformed_result,
        violations=violations,
    )
