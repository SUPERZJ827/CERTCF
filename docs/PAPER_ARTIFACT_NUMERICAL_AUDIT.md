# Paper–Artifact Numerical Audit

This audit compares the current SQJ manuscript (`issta2027/main.tex` and
`issta2027/appendix.tex`) with the compact public ledgers, expected-output
snapshots, README, and `scripts/reproduce_paper_tables.py`. It is a
read-only consistency audit: no experiment was rerun and no manuscript
number was changed. A `NOT_PUBLICLY_VERIFIABLE` entry means that the paper
reports a historical or implementation detail for which this lightweight
repository does not publish the underlying complete evidence; it is not a
claim that the paper value is wrong.

| Paper claim | Paper value | Artifact source | Artifact value | Status |
|---|---:|---|---:|---|
| R1 AppWorld exact pairs | 18 | `results_ledger.json` → R1 AppWorld | 18 | MATCH |
| R1 AppWorld attempted | 6 | `results_ledger.json` → R1 AppWorld | 6 | MATCH |
| R1 AppWorld certified invariant | 5 | `results_ledger.json` → R1 AppWorld | 5 | MATCH |
| R1 AgentDojo population pairs | 184 | `results_ledger.json` → R1 AgentDojo | 184 | MATCH |
| R1 AgentDojo covered tasks | 22 | `results_ledger.json` → R1 AgentDojo | 22 | MATCH |
| R1 AgentDojo certified invariant pairs | 157 | `results_ledger.json` → R1 AgentDojo | 157 | MATCH |
| R1 AgentDojo baseline-replay unknowns | 26 | `results_ledger.json` → R1 AgentDojo exclusions | 26 | MATCH |
| R1 AgentDojo failed relation instances | 1 | `results_ledger.json` → R1 AgentDojo exclusions | 1 | MATCH |
| R1 combined certified invariant controls | 162 | `results_ledger.json` → R1 AppWorld + AgentDojo | 5 + 157 = 162 | MATCH |
| Rejected R2 arguments scanned | 464 | `phase2_0_results.json` | 464 | MATCH |
| Rejected R2 literal candidates | 104 | `phase2_0_results.json` | 104 | MATCH |
| Rejected R2 candidate tasks | 69 | `phase2_0_results.json` | 69 | MATCH |
| Rejected R2 pilot tasks | 10 | `results_ledger.json` → R2 phase2_1 | 10 | MATCH |
| Rejected R2 apparent false acceptances | 5 | `phase2_2_results.json` | 5 | MATCH |
| Rejected R2 confirmed task-effect defects | 0 | `phase2_2_results.json` | 0 | MATCH |
| R2A AgentDojo certified and sensitive | 9 / 9 | `phase2_4_results.json` | 9 / 9 | MATCH |
| R2A ThinkingBox candidates | 30 | `phase3_2_results.json` | 30 | MATCH |
| R2A ThinkingBox certified and sensitive | 29 / 29 | `phase3_2_results.json` | 29 / 29 | MATCH |
| R2A combined primary certified violations | 38 | `results_ledger.json` → R2A | 38 | MATCH |
| R3 ThinkingBox calibration certified sensitive omissions | 5 | `phase4_1_results.json` | 5 | MATCH |
| R3v2 AppWorld certified sensitive omissions | 6 | `phase4_6_results.json` | 6 | MATCH |
| R3/R3v2 total certified sensitive omissions | 11 | `headline_counts.json` / reproduction script | 11 | MATCH |
| R3v2 ThinkingBox held-out tasks | 30 | `results_ledger.json` → R3/R3v2 | 30 | MATCH |
| R3v2 ThinkingBox strict candidate tasks | 4 | `results_ledger.json` → R3/R3v2 | 4 | MATCH |
| R4 usable tasks | 710 | `results_ledger.json` → R4 | 710 | MATCH |
| R4 strict static candidates | 2 | `results_ledger.json` → R4 | 2 | MATCH |
| R4 perturbation executions | 0 | `results_ledger.json` → R4 | 0 | MATCH |
| E1 modeled instances total | 124 | `validation_e1_results.json` | 124 | MATCH |
| E1 valid modeled instances | 120 | `validation_e1_results.json` | 120 | MATCH |
| E1 activated modeled instances | 102 | `validation_e1_results.json` | 102 | MATCH |
| E1 distinguished instances | 68 | `validation_e1_results.json` | 68 | MATCH |
| E1 survived instances | 34 | `validation_e1_results.json` | 34 | MATCH |
| E1 uncovered instances | 18 | `validation_e1_results.json` | 18 | MATCH |
| E1 baseline-invalid instances | 4 | `validation_e1_results.json` | 4 | MATCH |
| E1 trajectory executions | 0 | `validation_e1_results.json` | 0 | MATCH |
| E2 historical pairs | 49 | `validation_e2_final_results.json` | 49 | MATCH |
| E2 semantic violations | 43 | `validation_e2_final_results.json` | 43 | MATCH |
| E2 satisfied labels | 3 | `validation_e2_final_results.json` | 3 | MATCH |
| E2 ambiguous labels | 1 | `validation_e2_final_results.json` | 1 | MATCH |
| E2 insufficient labels | 2 | `validation_e2_final_results.json` | 2 | MATCH |
| E2 full-gate retained total | 38 | `validation_e2_final_results.json` | 38 | MATCH |
| E2 full-gate determinate retained validity | 37 / 37 | `validation_e2_final_results.json` | 37 / 37 | MATCH |
| E2 retained labeled violations | 37 / 43 = 86.0% | `validation_e2_final_results.json` | 37 / 43 = 86.0465% | MATCH |
| E3 AppWorld task IDs | 6 | `validation_e3_final_results.json` | 6 | MATCH |
| E3 semantic violations | 6 | `validation_e3_final_results.json` | 6 | MATCH |
| E3 certified and sensitive cases | 6 / 6 | `validation_e3_final_results.json` | 6 / 6 | MATCH |
| E3 focal effect family | playlist title | `validation_e3_final_results.json` | `spotify.playlists.title` | MATCH |
| AppWorld R3v2 scanned tasks | 147 | `phase5_0_results.json` (AppWorld reference replay) | 136 replay-available tasks; the compact R3v2 result publishes the 6-case calibration only | NOT_PUBLICLY_VERIFIABLE |
| AppWorld R3v2 passing baselines | 136 | `phase5_0_results.json` | 136 | MATCH |
| R3 ThinkingBox opportunity scan | 71 effects / 40 tasks | `results_ledger.json` → R3 diagnosis | 71 / 40 | MATCH |
| ToolSandbox R3v2 candidates | 0 | `results_ledger.json` → R3/R3v2 | 0 | MATCH |
| R4 benchmark usable-task breakdown | 477 ThinkingBox / 136 AppWorld / 97 AgentDojo | `phase5_0_results.json` | 477 / 136 / 97 | MATCH |
| R4 ThinkingBox static candidates | 2 | `phase5_0_results.json` | 2 | MATCH |
| E1 surviving counterfactuals | 17 | `validation_e1_results.json` plus paper derivation | not represented as a standalone compact field | NOT_PUBLICLY_VERIFIABLE |
| E2 literal-gate determinate validity | 43 / 46 = 93.5% | final aggregate source, G0 | 43 / 46 = 93.4783% | MATCH |
| E2 full-gate retained ambiguous case | 1 | `validation_e2_final_results.json` | 1 | MATCH |
| E2 excluded labeled violations | 6 | final aggregate E2 G3 cross-tab | 6 | MATCH |
| Implementation shared certification core | 445 LOC | `implementation_accounting.json` | 445 | MATCH |
| Implementation shared adapters | 59 LOC | `implementation_accounting.json` | 59 | MATCH |
| Implementation other package code | 608 LOC | `implementation_accounting.json` | 608 | MATCH |
| Research scripts | 14,025 LOC | `implementation_accounting.json` | 14,025 | MATCH |
| Tests | 865 LOC | `implementation_accounting.json` | 865 | MATCH |
| Retained evidence files | 17,539 | `implementation_accounting.json` | 17,539 | MATCH |
| Historical evidence size | approximately 86.8 GB | `implementation_accounting.json` | approximately 86.8 GB | MATCH |
| Static Python files counted | 114 | full historical accounting (not shipped) | not included in lightweight artifact | NOT_PUBLICLY_VERIFIABLE |
| Research script role totals | 18/10/7/1/45 modules; 3,221/3,508/888/51/6,357 LOC | full historical accounting (not shipped) | not included in lightweight artifact | NOT_PUBLICLY_VERIFIABLE |
| Evidence file-type breakdown | 6,840 DB / 9,553 JSON / 832 digest / 153 generated Python | full historical accounting (not shipped) | not included in lightweight artifact | NOT_PUBLICLY_VERIFIABLE |
| Independent-truth bridge admitted cases | 0 | public final ledger and limitation documentation | 0 admitted; no source mutation | MATCH |
| E4 selected tasks with complete requirement declarations | 24 selected; declaration failed | limitation documentation | failure documented, no compact execution result | NOT_PUBLICLY_VERIFIABLE |

## Audit result

The table contains 69 quantitative claims: 63 are `MATCH`, 6 are
`NOT_PUBLICLY_VERIFIABLE`, and 0 are `MISMATCH`. The latter status is absent
because no unexplained discrepancy was found. The one `NOT_PUBLICLY_VERIFIABLE`
AppWorld row is intentionally conservative: the public compact artifact
contains the 136 replay-available baseline count and the six-case R3v2
calibration, but not a standalone 147-task R3v2 scan ledger.

`python scripts/reproduce_paper_tables.py` checks the primary ledger and the
final E2/E3 compact ledgers against `headline_counts.json`; it does not claim
to regenerate the historical full record.
