# Quick artifacts

This directory contains compact projections of frozen historical results. The
files are intended for inspection and deterministic count verification, not as
a replacement for the full execution archive.

- `final_ledgers/`: small phase summaries and the final result ledger;
- `expected_outputs/`: the headline values checked by the verification script;
- `manifests/`: representative case identifiers and expected relation labels;
- `errata/`: the small reporting erratum relevant to the AppWorld R1 pilot.
- `expected_outputs/implementation_accounting.json`: historical static size
  accounting, not a runtime or labor estimate.

Run `python scripts/reproduce_paper_tables.py` from the repository root. No
benchmark, database, model cache, credential, or LLM API is required.
