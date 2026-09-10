from __future__ import annotations

from evaluator_audit.models import (
    ApplicabilityResult,
    ApplicabilityStatus,
    CandidateSolution,
    Task,
    ValidityEvidence,
)


class StripWhitespaceTransformation:
    name = "strip_whitespace"
    description = "strip leading and trailing whitespace from solution body"

    def check_applicability(self, task: Task, solution: CandidateSolution) -> ApplicabilityResult:
        if "\x00" in solution.body:
            return ApplicabilityResult(
                status=ApplicabilityStatus.UNKNOWN,
                reason="control character present, cannot prove whitespace trimming is semantics-preserving",
                evidence=ValidityEvidence(checks={"body_has_control_char": True}),
            )
        if solution.body != solution.body.strip():
            return ApplicabilityResult(
                status=ApplicabilityStatus.APPLICABLE,
                reason="solution has boundary whitespace",
                evidence=ValidityEvidence(
                    checks={
                        "original_length": len(solution.body),
                        "trimmed_length": len(solution.body.strip()),
                        "changed": solution.body != solution.body.strip(),
                    }
                ),
            )
        return ApplicabilityResult(
            status=ApplicabilityStatus.NOT_APPLICABLE,
            reason="solution already has no boundary whitespace",
            evidence=ValidityEvidence(checks={"changed": False}),
        )

    def apply(
        self, task: Task, solution: CandidateSolution, *, applicability: ApplicabilityResult | None = None
    ) -> CandidateSolution:
        _ = task
        if applicability and applicability.status != ApplicabilityStatus.APPLICABLE:
            return solution
        return solution.with_body(solution.body.strip())

    def as_dict(self, task: Task, solution: CandidateSolution):
        del task
        return {"applied_to": solution.solution_id}
