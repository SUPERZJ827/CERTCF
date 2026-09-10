#!/usr/bin/env python3
"""Phase 1.3 retry1 wrapper with the frozen representation-only adapter fix."""

from __future__ import annotations

import inspect
import json
from collections import Counter
from pathlib import Path

from agentdojo.base_tasks import BaseUserTask

import phase1_3_agentdojo_run as parent
from phase1_3_agentdojo_common import ROOT, file_digest, write_json
from phase1_3_retry1_adapter import persisted_call_to_runtime

OUT = ROOT / "artifacts" / "phase1_3_retry1"


def verify_retry1_freeze() -> tuple[list[dict], dict[str, dict]]:
    receipt = json.loads((OUT / "FREEZE_RECEIPT.json").read_text())
    checks = {
        "repaired_runner_sha256": Path(__file__),
        "adapter_sha256": ROOT / "scripts/phase1_3_retry1_adapter.py",
        "adapter_fix_audit_sha256": OUT / "ADAPTER_FIX_AUDIT.json",
        "eligible_candidate_artifact_sha256": OUT / "ELIGIBLE_CANDIDATES_V1.json",
        "representative_artifact_sha256": OUT / "TASK_REPRESENTATIVES_V1.json",
        "full_queue_sha256": OUT / "PILOT_QUEUE_FULL_V1.json",
        "pilot_queue_sha256": OUT / "PILOT_QUEUE_V1.json",
        "queue_identity_check_sha256": OUT / "QUEUE_IDENTITY_CHECK.json",
        "transformation_specification_sha256": OUT / "TRANSFORMATION_SPECIFICATION_V1.txt",
        "validity_specification_sha256": OUT / "VALIDITY_CERTIFICATE_SPECIFICATION_V1.txt",
        "evaluator_relation_specification_sha256": OUT / "EVALUATOR_RELATION_SPECIFICATION_V1.txt",
        "instrumentation_sha256": ROOT / "scripts/phase1_3_agentdojo_common.py",
    }
    for field, path in checks.items():
        if file_digest(path) != receipt[field]:
            raise RuntimeError(f"Retry1 freeze verification failed: {field}")
    queue_check = json.loads((OUT / "QUEUE_IDENTITY_CHECK.json").read_text())
    if queue_check.get("equal") is not True:
        raise RuntimeError("Retry1 queue identity check failed")
    queue = json.loads((OUT / "PILOT_QUEUE_V1.json").read_text())
    eligible = json.loads((OUT / "ELIGIBLE_CANDIDATES_V1.json").read_text())
    return queue, {record["candidate_id"]: record for record in eligible}


def retry1_evaluate(suite, task, initial, execution) -> dict:
    trace = [persisted_call_to_runtime(call) for call in execution["function_stack"]]
    verdict = suite._check_task_result(
        task,
        execution["model_output"] or [],
        initial,
        suite.environment_type.model_validate(execution["final_state"]),
        trace,
    )
    overridden = type(task).utility_from_traces is not BaseUserTask.utility_from_traces
    return {
        "official_verdict": bool(verdict),
        "utility_from_traces_implemented": overridden,
        "official_path_used": "utility_from_traces" if overridden else "utility",
        "state_based_utility_outcome": bool(verdict) if not overridden else None,
        "trace_based_utility_outcome": bool(verdict) if overridden else None,
        "task_utility_source_file": inspect.getsourcefile(type(task)),
        "adapter": "arguments_to_args",
    }


def main() -> None:
    queue, eligible = verify_retry1_freeze()
    parent.OUT = OUT
    parent.evaluate = retry1_evaluate
    for item in queue:
        parent.run_case(eligible[item["candidate_id"]])
    results = parent.recompute_results(queue)
    results.update({"parent_attempt": "phase1_3", "retry_index": 1, "adapter_fix": "arguments_to_args"})
    write_json(OUT / "results.json", results)

    rows = []
    for item in queue:
        base = OUT / "cases" / item["candidate_id"]
        cert = json.loads((base / "validity_certificate.json").read_text())
        evaluator = json.loads((base / "transformed/evaluator.json").read_text()) if (base / "transformed/evaluator.json").exists() else {}
        rows.append(f"| {item['candidate_id']} | {item['suite']} | {item['task_id']} | {cert['classification']} | {evaluator.get('consistency_classification', 'NOT_COMPARED')} | {cert.get('reason') or ''} |")
    report = "# Phase 1.3 Retry1: AgentDojo Blind Dual Replay V1\n\n## Provenance\n\nThe parent attempt was `ABORTED_PRE_TRANSFORMED_ADAPTER_ERROR` after one original trajectory execution, zero original utility outcomes, and zero transformed executions/outcomes. Retry1 starts every case, including the first, from a fresh official environment. The only implementation repair maps persisted `arguments` to runtime `args`.\n\n## Frozen protocol\n\nThe 184-pair eligible population, selection algorithm, 26 task representatives, ordered ten-task pilot, transformation, semantic-order rules, validity criteria, canonicalization, evaluator relation, and stop rule are unchanged. The retry1 receipt was written before any retry1 trajectory execution.\n\n## Results\n\n" + "\n".join(f"- {key}: {value}" for key, value in results.items() if key not in {"exclusion_reasons", "gate_decisions"}) + "\n\nGate: " + ", ".join(f"`{gate}`" for gate in results["gate_decisions"]) + "\n\n## Per-case evidence\n\n| Case | Suite | Task | Validity | Evaluator relation | Exclusion |\n|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n\n## Exclusions\n\n" + json.dumps(results["exclusion_reasons"], sort_keys=True) + "\n\n## Stop condition\n\nThe complete frozen ten-task pilot is persisted. No LLM API was called, no issue/PR search was performed, and the candidate population was not expanded. Any disagreement is labelled only `EVALUATOR_VIOLATION_CANDIDATE`. No full-population experiment follows.\n"
    (ROOT / "PHASE1_3_RETRY1_AGENTDOJO_BLIND_DUAL_REPLAY_V1.md").write_text(report)


if __name__ == "__main__":
    main()
