# Result Provenance

The compact ledger at `artifacts/quick/final_ledgers/results_ledger.json` is the
source for the headline values reproduced by
`scripts/reproduce_paper_tables.py`. The ledger was derived from persisted
historical artifacts and case-level summaries; it is not a newly rerun
experiment.

## Primary relation results

| Paper result | Compact source | Interpretation |
|---|---|---|
| R1 AppWorld | `phase0_9_retry2_results.json` and final ledger | Five certified invariant controls; one construction failure; twelve candidates not consumed after the stop rule |
| R1 AgentDojo | `results_ledger.json` (`relations.R1.agentdojo`) | 184 population pairs, 157 certified invariant pairs, 22 covered tasks, 26 baseline-replay exclusions, one final-state mismatch |
| Rejected R2 | `phase2_0_results.json`, `phase2_2_results.json`, and final ledger | 464 ground-truth arguments scanned, 104 literal candidates across 69 tasks; five apparent false acceptances audited; zero confirmed task-effect defects |
| R2A AgentDojo | `phase2_4_results.json` | Nine of nine certified and sensitive held-out cases |
| R2A ThinkingBox | `phase3_2_results.json` and final ledger | 29 of 30 certified candidates; all 29 admitted cases sensitive |
| R3 ThinkingBox | `phase4_1_results.json` | Five certified sensitive omissions and five baseline-gate unknowns |
| R3v2 AppWorld | `phase4_6_results.json` | Six certified sensitive omissions in the small calibration |
| R4 | `phase5_0_results.json` and final ledger | 710 usable tasks, two strict static candidates, zero executions |

## Supplementary and failed-strengthening results

| Result | Compact source | Status and boundary |
|---|---|---|
| E1 | `validation_e1_results.json` | Modeled-fault controls applied to persisted evidence; 68 of 102 activated instances distinguished; no source mutation or official evaluator rerun |
| E2 | `validation_e2_final_results.json` (derived from `validation_llm_adjudication_claude_v1/FINAL_RESULTS.json`) | Final 49-pair auxiliary adjudication: 43 violations, 3 satisfied, 1 ambiguous, 2 insufficient; full gate retains 38 total, including 37/37 determinate valid cases and 37/43 labeled violations. `validation_e2_results.json` is preserved as the historical preliminary five-labeled/44-unlabeled artifact. |
| E3 | `validation_e3_final_results.json` (derived from `validation_llm_adjudication_claude_v1/FINAL_RESULTS.json`) | Final auxiliary adjudication for six AppWorld task IDs: all six labeled violations; all six certified and evaluator-sensitive in the single playlist-title effect family. `validation_e3_retry1_results.json` is preserved as the historical execution ledger whose semantic-adjudication field was pending. |
| E4 | `docs/ARTIFACT_COMPLETENESS_AUDIT.md` | Failed strengthening attempt documented only; no new source mutants executed |
| Independent-truth bridge | final ledger and audit notes | Stopped before mutation because an independent common truth pool was not established |

## Opportunity funnels and accounting

The final ledger records the distinct units for each funnel. In particular,
candidate pairs, tasks, certified cases, and executions must not be combined as
one denominator. The historical implementation accounting reports 445 lines
of shared certification core, 14,025 lines of research scripts, 17,539
retained evidence files, and approximately 86.8 GB of historical evidence.
These are accounting facts, not scientific performance metrics.

## Evidence roles

- **Prospective primary evidence:** AgentDojo R1, AgentDojo/ThinkingBox R2A,
  and executed R3 calibration cases.
- **Calibration evidence:** AppWorld R1 controls, ThinkingBox R3 diagnosis,
  and the AppWorld R3v2 six-case calibration.
- **Opportunity-only evidence:** ToolSandbox R3v2, R3v2 held-out scans, and
  R4.
- **Supplementary controls:** E1, E2, and E3.
- **Failed strengthening attempts:** E4 and the independent-truth bridge.

No result in this repository should be read as evidence of general evaluator
correctness or as an implementation-level mutation score.
