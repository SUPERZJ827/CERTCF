from __future__ import annotations

from typing import Any, Mapping, Protocol

from ..models import ApplicabilityResult, CandidateSolution, Task


class BaseTransformation(Protocol):
    name: str
    description: str

    def check_applicability(
        self, task: Task, solution: CandidateSolution,
    ) -> ApplicabilityResult:
        ...

    def apply(
        self, task: Task, solution: CandidateSolution, *, applicability: ApplicabilityResult | None = None
    ) -> CandidateSolution:
        ...

    def as_dict(self, task: Task, solution: CandidateSolution) -> Mapping[str, Any]:
        ...
