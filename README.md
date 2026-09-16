# CERTCF

Evidence-gated counterfactual testing of AI agent benchmark evaluators.

## Overview

An agent benchmark evaluator is a test oracle: it decides whether an agent
completed a task. A syntactically valid counterfactual is not necessarily a
valid evaluator test. Changing an argument, swapping calls, or deleting a
call may leave the user-visible task unchanged, may be repaired later, or may
affect the wrong entity.

CERTCF makes this boundary explicit:

```text
generate -> replay -> certify -> compare -> confirm
```

The original and counterfactual executions are replayed from independently
rebuilt initial states. Requests, responses, task-relevant state projections,
outputs, and compensation checks are recorded before the evaluator verdict is
interpreted. A candidate is classified as certified, failed, or unknown. Only
certified relation instances enter the evaluator comparison.

The certificate is an operational sufficient condition under stated grounding
and observability assumptions. It is not a complete formalization of natural
language task meaning.

## Paper

**Testing the Test Oracle: Evidence-Gated Counterfactual Testing of AI Agent
Benchmark Evaluators**

The manuscript is under preparation. No publication metadata is asserted here.

## Main findings

- The rejected literal-based R2 produced five apparent false acceptances; the
  independent audit confirmed zero task-effect evaluator defects (three
  acceptances were justified, one task was ambiguous, and one certificate was
  invalid).
- In the certified AgentDojo R1 population, 157/157 call permutations kept the
  evaluator verdict invariant.
- In the certified R2A population, 38/38 task-critical effect violations were
  rejected by the studied evaluators.
- In the executed certified R3/R3v2 cases, 11/11 isolated omissions were
  rejected.
- Stronger validity requirements substantially reduce admissible opportunities:
  for example, the ThinkingBox R3v2 scan reduced 30 held-out tasks to four
  strict candidate tasks, while the R4 scan found two strict static candidates
  among 710 usable tasks and executed none.

These are relation- and benchmark-scoped observations. They do not establish
general evaluator correctness, benchmark defect prevalence, general fault
recall, or implementation-level mutation effectiveness.

## Repository structure

```text
src/evaluator_audit/   Core data models, evaluator adapter, runner, and contracts
examples/toy/          Small deterministic end-to-end example
scripts/               Benchmark protocols and historical result analyses
tests/                 Unit and protocol-contract tests
artifacts/quick/       Compact frozen ledgers and representative metadata
docs/                  Artifact, provenance, and reproducibility notes
```

## Installation

The tested baseline is Python 3.11.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Benchmark integrations are optional and should be installed in separate
environments because their dependencies conflict:

```bash
python -m pip install -e '.[agentdojo]'
python -m pip install -e '.[appworld]'
python -m pip install -e '.[thinkingbox]'
```

The pinned upstream commits are listed in
[docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md). Installing an extra does
not download benchmark data automatically.

## Unit tests

Run the dependency-free test suite with:

```bash
python -m pytest -q
```

Tests that require an optional benchmark are skipped when that benchmark is
not installed. The toy runner and core contracts run without benchmark data.

## Lightweight result verification

The compact ledgers in `artifacts/quick/final_ledgers/` are projections of the
historical frozen result records. Recompute the headline table with:

```bash
python scripts/reproduce_paper_tables.py
```

This command reads the included ledger; it does not rerun benchmark agents,
call an LLM API, or claim that the full historical record is present.

## Representative end-to-end example

The dependency-free toy example exercises transformation applicability,
evaluation, timeout/error capture, and structured evidence:

```bash
python scripts/reproduce_toy_case.py
```

The benchmark-specific R1, R2, R2A, and R3/R3v2 protocols remain available in
`scripts/`. Their exact benchmark inputs, reset environments, and full case
bundles are not distributed here. The compact representative manifests under
`artifacts/quick/manifests/` identify cases and expected relation outcomes;
they are provenance records, not a claim that those benchmark executions can
be reproduced from this repository alone.

## Benchmarks and versions

The historical study used AppWorld, AgentDojo, ThinkingBox, ThinkingBox data,
and ToolSandbox. The pinned commits and the distinction between executable,
ledger-verifiable, and historical-only results are documented in
[docs/RESULT_PROVENANCE.md](docs/RESULT_PROVENANCE.md).

## Artifact scope

### Lightweight verification

Small JSON ledgers, errata, expected headline values, and case metadata are
included so reviewers can inspect provenance and recompute selected counts.

### Representative reproduction

The core implementation and the deterministic toy pipeline are runnable from a
clean checkout. Benchmark protocol scripts are preserved for users who have
the corresponding pinned upstream environments and data.

### Full historical audit record

The complete research workspace is intentionally not in GitHub. It contains
approximately 17,539 evidence files and 86.8 GB of database snapshots,
execution traces, benchmark data, and intermediate artifacts. See
[docs/FULL_AUDIT_MANIFEST.md](docs/FULL_AUDIT_MANIFEST.md).

## Limitations

- No confirmed real evaluator defect was found in the certified denominators.
- The results do not establish general evaluator correctness or real-defect
  recall.
- E1 is a **modeled-fault control on persisted evidence**, not source mutation
  and re-execution of an official evaluator. Its historical filenames may use
  `mutation` or `mutant`, but those are legacy identifiers.
- Semantic onboarding is benchmark-specific and depends on grounding,
  observability, replayability, and stable task-effect predicates.
- E2 is retrospective and phase-confounded; its semantic labels came from one
  outcome-blind LLM adjudicator without human calibration.
- R4 is an opportunity scan only: no R4 perturbation or evaluator outcome was
  executed.
- Some benchmark data and historical artifacts are too large or
  redistribution-constrained for this repository.

## Citation

Please cite the manuscript title above until final publication metadata are
available.

## License

No new license is asserted by this release. Add a repository license before
redistributing the code if the project policy requires one.
