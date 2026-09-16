# Reproducibility Guide

## 1. Supported environment

- Python: 3.11 (the historical runs used Python 3.11.x).
- Operating system: Linux was used for the recorded runs; platform details
  are retained in historical freeze receipts.
- Core package dependencies: none beyond the Python standard library.
- Test dependencies: `pytest` and `PyYAML` through the `dev` extra.

Create the baseline environment:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

## 2. Unit tests and static checks

```bash
python -m pytest -q
make check
```

The complete test suite skips optional benchmark adapters when the corresponding
package is absent. No LLM API is needed for the core tests or ledger scripts.

## 3. Lightweight result verification

```bash
python scripts/reproduce_paper_tables.py
```

The command reads only `artifacts/quick/final_ledgers/` and prints the headline
counts. It does not rerun an agent, mutate an evaluator, access a private
service, or download a benchmark.

## 4. End-to-end implementation example

```bash
python scripts/reproduce_toy_case.py
```

This deterministic example exercises an applicable semantics-preserving
transformation, baseline/transformed evaluation, structured records, and
explicit timeout/error handling. It is an implementation reproduction, not a
claim that the toy task estimates benchmark performance.

## 5. Benchmark versions

The historical benchmark pins are:

| Benchmark | Commit or data revision |
|---|---|
| AgentDojo | `357c80dea9af34323f709c3505d9e6d224654c7e` |
| AppWorld | `42b5bcf3cd334fee33f0c37d02070a9f5807add5` |
| ThinkingBox | `40c1212f9582ca90175079bc313e530e9e9a4981` |
| ThinkingBox data | `fcaba4c1a9debec42fda7f15bf29fe6d6b46c431` |
| ToolSandbox | `165848b9a78cead7ca7fe7c89c688b58e6501219` |

Use `python scripts/prepare_references.py <name>` to fetch a pinned upstream
repository. This command prepares source references only; it does not run an
experiment. Benchmark data and runtime databases must be obtained according to
the upstream project's instructions.

## 6. Historical protocol scripts

The `phase*` scripts preserve the original protocol logic and expected input
contracts. They require benchmark-specific data, resettable environments,
frozen queues, and preceding phase outputs that are intentionally not included
in this lightweight repository. Do not report a fresh run of a phase script as
the original frozen result unless its inputs, commit, and receipts match.

## 7. Known limitations

- The full historical experiment is not a one-command reproduction.
- No LLM adjudication script should be run without an explicit user-provided
  CLI, prompt, and access policy.
- E1 is a modeled-fault control on saved evidence, not source mutation and
  execution of the official evaluator.
- E2 is retrospective, phase-confounded, and incompletely independently
  adjudicated.
- R4 has no executed counterfactual or evaluator result.
- Benchmark-specific adapters require their upstream licenses, data, and
  dependency environments.

## 8. Expected resources

The core tests and quick verification are small and finish in seconds on a
normal development machine. Benchmark preparation and replay costs depend on
the upstream benchmark and are not estimated here because the full historical
experiments are not rerun by this release.
