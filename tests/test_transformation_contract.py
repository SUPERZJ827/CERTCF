from evaluator_audit.models import (
    ApplicabilityStatus,
    CandidateSolution,
    Task,
)
from examples.toy.transformations import StripWhitespaceTransformation


def _task() -> Task:
    return Task(task_id="t1", prompt="p", expected_output="x")


def test_unknown_if_control_character_is_present():
    transformation = StripWhitespaceTransformation()
    solution = CandidateSolution(solution_id="s1", task_id="t1", body="x\x00")
    result = transformation.check_applicability(_task(), solution)
    assert result.status == ApplicabilityStatus.UNKNOWN
    assert result.reason is not None


def test_apply_is_reproducible():
    transformation = StripWhitespaceTransformation()
    solution = CandidateSolution(solution_id="s1", task_id="t1", body="  x ")
    applicability = transformation.check_applicability(_task(), solution)
    assert applicability.status == ApplicabilityStatus.APPLICABLE
    first = transformation.apply(_task(), solution, applicability=applicability)
    second = transformation.apply(_task(), solution, applicability=applicability)
    assert first.body == second.body
    assert first.body == "x"
