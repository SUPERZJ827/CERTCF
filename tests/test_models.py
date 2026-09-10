from evaluator_audit.models import (
    ApplicabilityStatus,
    EvaluatorOutcome,
    CandidateSolution,
    Task,
    ResultDiff,
)


def test_enums_and_statuses_cover_required_values():
    assert ApplicabilityStatus.APPLICABLE.value == "APPLICABLE"
    assert ApplicabilityStatus.UNKNOWN.value == "UNKNOWN"
    assert EvaluatorOutcome.TIMEOUT.value == "TIMEOUT"


def test_candidate_digest_is_deterministic():
    task = Task(task_id="t1", prompt="p", expected_output="x")
    first = CandidateSolution(solution_id="s1", task_id=task.task_id, body="  x ")
    second = CandidateSolution(solution_id="s1", task_id=task.task_id, body="  x ")
    assert first.digest == second.digest


def test_diff_detects_output_change():
    diff = ResultDiff(
        baseline_status=EvaluatorOutcome.PASS,
        transformed_status=EvaluatorOutcome.FAIL,
        baseline_output="a",
        transformed_output="b",
    )
    assert diff.differs
