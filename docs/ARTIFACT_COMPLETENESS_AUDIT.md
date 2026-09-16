# Artifact Completeness Audit

This audit separates what can be rerun from this repository, what can be
recomputed from compact frozen ledgers, and what is documented from the
historical research workspace only. It intentionally does not expand the
repository with the full execution archive.

## Already public

- The core Python package in `src/evaluator_audit/`.
- The deterministic toy evaluator, transformation, runner, and tests.
- The benchmark protocol and analysis scripts under `scripts/`.
- Pinned upstream references in `pyproject.toml` and
  `scripts/prepare_references.py`.
- Compact result projections under `artifacts/quick/`.

## Available locally and useful for public reproduction

The local research workspace contains the frozen result ledger, per-phase
result summaries, freeze receipts, selected queues, and paper result tables.
Only small, non-sensitive projections were selected for this repository:

- `artifacts/final/results_ledger.json` and phase-level `results.json` files;
- the Phase 0.9 reporting erratum;
- benchmark/task identifiers, transformation descriptions, expected relation
  statuses, and digests in `artifacts/quick/manifests/`;
- implementation and unit-test code already present in the public repository.

## Available locally but too large for GitHub

The historical workspace contains approximately 17,539 retained evidence files
and approximately 86.8 GB of material, including database snapshots, complete
execution traces, branch states, response payloads, and intermediate queues.
Those files are not needed to recompute the compact headline ledger and are not
included.

## Third-party or redistribution-constrained

AppWorld, AgentDojo, ThinkingBox, ThinkingBox data, and ToolSandbox remain
external dependencies. Their repositories are referenced by pinned commit, but
their datasets, caches, databases, and environments are not vendored here.
Some historical traces also contain benchmark-native state and are therefore
not redistributed.

## Not found or not independently reproducible from this checkout

- A complete independent semantic-truth pool for all relations.
- A full source-level mutation-and-reexecution study of the official
  evaluators.
- A full R4 execution: the R4 scan stopped at the opportunity gate.
- The full E2 adjudication population with independent human labels.
- The complete historical execution bundle for every certified case.

## Recommended public artifact set

| Result | Public status | Evidence level |
|---|---|---|
| R1 AppWorld | Compact ledger and case metadata | Historical result; toy/core execution is rerunnable |
| R1 AgentDojo | Compact ledger and case metadata | Historical result; protocol script requires pinned benchmark/data |
| Rejected R2 | Compact audit ledger and false-signal metadata | Recomputable classification summary; full replay historical-only |
| R2A AgentDojo | Compact result ledger and representative metadata | Historical result; full replay requires benchmark/data |
| R2A ThinkingBox | Compact result ledger and representative metadata | Historical result; full replay requires benchmark/data |
| R3 ThinkingBox | Compact calibration ledger | Historical result; five certified cases and five unknowns |
| R3v2 AppWorld | Compact calibration ledger and case metadata | Historical result; full replay requires AppWorld data |
| R4 opportunity scan | Compact opportunity ledger | Opportunity-only; no perturbation executed |
| E1 modeled-fault controls | Compact result ledger | Persisted-evidence control, not source mutation |
| E2 retrospective labels | Compact result ledger | Historical, phase-confounded, single-adjudicator analysis |
| E3 AppWorld extension | Compact result ledger | Six-case prospective subset; full replay requires AppWorld |
| E4 failed strengthening | Documented in provenance only | Failed feasibility attempt; no source mutants executed |
| Independent-truth bridge | Documented in provenance only | Stopped at its independence gate |
| Opportunity funnels | Derived in the final ledger | Recomputable from the included compact ledger |
| Implementation accounting | Documented in provenance only | Historical accounting, not a runtime benchmark |

The repository therefore supports lightweight verification and selected
implementation-level examples, but does not claim full end-to-end regeneration
of every historical paper result.
