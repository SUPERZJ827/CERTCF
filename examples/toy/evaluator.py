from __future__ import annotations

from evaluator_audit.models import Task, CandidateSolution


def toy_evaluator(task: Task, solution: CandidateSolution) -> bool:
    """
    Baseline toy semantics:
    - evaluate PASS when solution body (stripped) exactly equals task.expected_output.
    - evaluate FAIL otherwise.
    """
    if solution.body == "error":
        raise ValueError("toy evaluator requested error")
    if solution.body == "timeout":
        import time
        time.sleep(5.0)
    return solution.body.strip() == task.expected_output
