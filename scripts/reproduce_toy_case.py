#!/usr/bin/env python3
"""Run the dependency-free CERTCF toy pipeline and print a compact record."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from examples.toy.evaluator import toy_evaluator
from examples.toy.transformations import StripWhitespaceTransformation
from evaluator_audit.evaluators import CallableEvaluatorAdapter, ExecutionConfig
from evaluator_audit.models import CandidateSolution, Task
from evaluator_audit.runner import run_audit_cycle


def main() -> int:
    task = Task(task_id="public-toy-1", prompt="normalize whitespace", expected_output="42")
    baseline = CandidateSolution(solution_id="baseline", task_id=task.task_id, body=" 42 ")
    record = run_audit_cycle(
        task=task,
        baseline_solution=baseline,
        transformation=StripWhitespaceTransformation(),
        evaluator=CallableEvaluatorAdapter(toy_evaluator),
        config=ExecutionConfig(timeout_seconds=1.0),
    )
    payload = record.to_dict()
    summary = {
        "applicability": payload["applicability"]["status"],
        "baseline_status": payload["baseline_result"]["status"],
        "transformed_status": payload["transformed_result"]["status"],
        "overall_status": payload["record_summary"]["status"],
        "violations": payload["record_summary"]["violations"],
    }
    expected = {
        "applicability": "APPLICABLE",
        "baseline_status": "PASS",
        "transformed_status": "PASS",
        "overall_status": "PASS",
        "violations": 0,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    if summary != expected:
        print("TOY REPRODUCTION: FAILED")
        return 1
    print("TOY REPRODUCTION: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
