from __future__ import annotations

from typing import Any, Mapping

from examples.toy.evaluator import toy_evaluator
from examples.toy.transformations import StripWhitespaceTransformation
from evaluator_audit.evaluators import CallableEvaluatorAdapter, ExecutionConfig
from evaluator_audit.models import (
    ApplicabilityStatus,
    ApplicabilityResult,
    CandidateSolution,
    EvaluatorOutcome,
    Task,
    ValidityEvidence,
)
from evaluator_audit.runner import run_audit_cycle


class IdentityTransformation:
    name = "identity"
    description = "identity mapping used for testing"

    def check_applicability(self, task: Task, solution: CandidateSolution) -> ApplicabilityResult:
        return ApplicabilityResult(
            status=ApplicabilityStatus.APPLICABLE,
            reason="test transformation",
            evidence=ValidityEvidence(checks={"unchanged": solution.body == solution.body}),
        )

    def apply(self, task: Task, solution: CandidateSolution, *, applicability: ApplicabilityResult | None = None) -> CandidateSolution:
        del applicability, task
        return solution

    def as_dict(self, task: Task, solution: CandidateSolution) -> Mapping[str, Any]:
        del task
        return {"mode": "identity", "source": solution.body}


def test_toy_case_produces_full_structured_record():
    task = Task(task_id="toy-1", prompt="normalize whitespace", expected_output="42")
    baseline = CandidateSolution(solution_id="base", task_id="toy-1", body=" 42 ")
    evaluator = CallableEvaluatorAdapter(toy_evaluator)
    config = ExecutionConfig(timeout_seconds=1.0)

    record = run_audit_cycle(
        task=task,
        baseline_solution=baseline,
        transformation=StripWhitespaceTransformation(),
        evaluator=evaluator,
        config=config,
    )

    payload = record.to_dict()
    assert payload["transformation"] == "strip_whitespace"
    assert payload["task"]["task_id"] == "toy-1"
    assert payload["baseline_solution"]["body"] == " 42 "
    assert payload["transformed_solution"]["body"] == "42"
    assert payload["record_summary"]["status"] == "PASS"
    assert payload["record_summary"]["violations"] == 0


def test_unknown_applicability_does_not_run_transformed_branch():
    task = Task(task_id="toy-2", prompt="unknown control", expected_output="42")
    baseline = CandidateSolution(solution_id="base", task_id="toy-2", body="42\x00")
    evaluator = CallableEvaluatorAdapter(toy_evaluator)

    record = run_audit_cycle(
        task=task,
        baseline_solution=baseline,
        transformation=StripWhitespaceTransformation(),
        evaluator=evaluator,
    )

    assert record.applicability.status == ApplicabilityStatus.UNKNOWN
    assert record.baseline_result is None
    assert record.transformed_result is None


def test_timeout_and_error_results_from_evaluator():
    evaluator = CallableEvaluatorAdapter(toy_evaluator)

    timeout_task = Task(task_id="toy-time", prompt="timeout", expected_output="x")
    timeout_baseline = CandidateSolution(solution_id="timeout", task_id="toy-time", body="timeout")
    timeout_record = run_audit_cycle(
        task=timeout_task,
        baseline_solution=timeout_baseline,
        transformation=IdentityTransformation(),
        evaluator=evaluator,
        config=ExecutionConfig(timeout_seconds=0.05),
    )
    assert timeout_record.baseline_result is not None
    assert timeout_record.transformed_result is not None
    assert timeout_record.baseline_result.status == EvaluatorOutcome.TIMEOUT
    assert timeout_record.transformed_result.status == EvaluatorOutcome.TIMEOUT

    error_task = Task(task_id="toy-error", prompt="err", expected_output="x")
    error_baseline = CandidateSolution(solution_id="error", task_id="toy-error", body="error")
    error_record = run_audit_cycle(
        task=error_task,
        baseline_solution=error_baseline,
        transformation=IdentityTransformation(),
        evaluator=evaluator,
    )
    assert error_record.baseline_result is not None
    assert error_record.transformed_result is not None
    assert error_record.baseline_result.status == EvaluatorOutcome.ERROR
    assert error_record.transformed_result.status == EvaluatorOutcome.ERROR


def test_evaluator_exec_config_is_identical_for_baseline_and_transformed():
    task = Task(task_id="toy-3", prompt="config", expected_output="v")
    baseline = CandidateSolution(solution_id="base", task_id="toy-3", body="v")
    evaluator = CallableEvaluatorAdapter(toy_evaluator)
    config = ExecutionConfig(timeout_seconds=0.75, extra_env={"A": "1", "B": "2"})

    record = run_audit_cycle(
        task=task,
        baseline_solution=baseline,
        transformation=IdentityTransformation(),
        evaluator=evaluator,
        config=config,
    )

    assert record.baseline_result is not None
    assert record.transformed_result is not None
    assert record.baseline_result.execution_config == record.transformed_result.execution_config
    assert record.baseline_result.execution_config["timeout_seconds"] == config.timeout_seconds
