#!/usr/bin/env python3
"""Build a self-contained, outcome-blinded E2 semantic adjudication packet."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
E2 = ROOT / "artifacts" / "validation_e2"
OUT = ROOT / "artifacts" / "validation_e2_adjudication_v2"
ALLOWED = {
    "trajectory.json",
    "execution.json",
    "initial_state.json",
    "final_state.json",
    "requests.json",
    "responses.json",
    "response_trace.json",
    "function_stack.json",
    "final_output.json",
}
FORBIDDEN_METADATA_TOKENS = {
    "phase2_1", "phase2_4", "phase3_2", "r2", "r2a", "gate",
    "certificate", "classification", "sensitive", "evaluator",
}


def load(path: Path):
    return json.loads(path.read_text())


def load_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


if OUT.exists():
    raise RuntimeError("v2 adjudication packet exists; refusing overwrite")
OUT.mkdir(parents=True)
pool = load(E2 / "SHARED_CANDIDATE_POOL_V1.json")

p21 = {row["candidate_id"]: row for row in load(ROOT / "artifacts" / "phase2_1" / "R2_PILOT_QUEUE_V1.json")}
p24 = {row["candidate_id"]: row for row in load_jsonl(ROOT / "artifacts" / "phase2_3" / "r2a_effect_candidates.jsonl")}
p32_source = load_jsonl(ROOT / "artifacts" / "phase3_0" / "thinkingbox_r2a_static_candidates.jsonl")


def prompt_for(row):
    if row["source_phase"] == "phase2_1":
        return p21[row["case_id"]]["prompt"]
    if row["source_phase"] == "phase2_4":
        return p24[row["case_id"]]["prompt"]
    matches = [
        item for item in p32_source
        if item["task_id"] == row["task_id"]
        and item["call_index"] == row["target_call_index"]
        and item["argument_name"] == row["target_argument"]
    ]
    if len(matches) != 1:
        raise RuntimeError(f"prompt match count {len(matches)} for {row['case_id']}")
    return matches[0]["prompt"]


manifest = []
for row in sorted(pool, key=lambda item: digest(["blind-v2", item["unit_id"]])):
    alias = "case_" + digest(["blind-v2", row["unit_id"]])[:12]
    case_out = OUT / "cases" / alias
    case_out.mkdir(parents=True)
    source = ROOT / row["evidence_root"]
    branches = {"reference": "original", "counterfactual": row["changed_branch"]}
    copied = {}
    for neutral, actual in branches.items():
        branch_out = case_out / neutral
        branch_out.mkdir()
        copied[neutral] = []
        branch_source = source / actual
        if not branch_source.exists():
            continue
        for src in sorted(branch_source.iterdir()):
            if src.is_file() and src.name in ALLOWED:
                shutil.copyfile(src, branch_out / src.name)
                copied[neutral].append(src.name)
    task = {
        "case_alias": alias,
        "instruction": prompt_for(row),
        "branches": {
            "reference": "official/reference execution",
            "counterfactual": "changed execution to adjudicate",
        },
        "adjudication_questions": [
            "Does the reference execution satisfy the explicit user requirements?",
            "Does the counterfactual violate an explicit user requirement? If yes, identify it verbatim.",
            "Is the changed action causally connected to a persistent wrong task effect?",
            "Is the correct effect absent, with no compensating action?",
            "Is the task interpretation ambiguous or the evidence insufficient?",
        ],
    }
    (case_out / "task.json").write_text(json.dumps(task, indent=2, sort_keys=True) + "\n")
    manifest.append({"case_alias": alias, "copied_files": copied})

(OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
(OUT / "ADJUDICATOR_INSTRUCTIONS.md").write_text(
    "# Independent semantic adjudication\n\n"
    "Judge task semantics from the instruction, complete trajectories, tool responses, and raw states. "
    "Do not seek evaluator outcomes, prior certificates, gate labels, or mutant results. Use one of: "
    "SEMANTIC_VIOLATION_CONFIRMED, REQUIREMENT_SATISFIED, TASK_AMBIGUOUS, EVIDENCE_INSUFFICIENT. "
    "Record the exact instruction span and evidence paths supporting each decision.\n"
)

prompt_complete = 0
state_complete = 0
trajectory_complete = 0
response_complete = 0
metadata_leaks = []
for entry in manifest:
    case_dir = OUT / "cases" / entry["case_alias"]
    task_text = (case_dir / "task.json").read_text().lower()
    if load(case_dir / "task.json")["instruction"]:
        prompt_complete += 1
    if all((case_dir / branch / "trajectory.json").exists() for branch in ["reference", "counterfactual"]):
        trajectory_complete += 1
    if all(
        (case_dir / branch / "initial_state.json").exists()
        and (case_dir / branch / "final_state.json").exists()
        for branch in ["reference", "counterfactual"]
    ):
        state_complete += 1
    if all(
        (case_dir / branch / "responses.json").exists()
        or (case_dir / branch / "response_trace.json").exists()
        for branch in ["reference", "counterfactual"]
    ):
        response_complete += 1
    for token in FORBIDDEN_METADATA_TOKENS:
        if re.search(rf"\b{re.escape(token)}\b", task_text) or token in entry["case_alias"].lower():
            metadata_leaks.append({"case_alias": entry["case_alias"], "token": token})

audit = {
    "version": 2,
    "cases": len(manifest),
    "full_instruction_available": prompt_complete,
    "complete_trajectory_pairs": trajectory_complete,
    "complete_initial_final_state_pairs": state_complete,
    "complete_response_trace_pairs": response_complete,
    "evaluator_files_copied": 0,
    "certificate_files_copied": 0,
    "source_phase_or_gate_metadata_leaks": metadata_leaks,
    "prior_group_names_hidden": not metadata_leaks,
    "outcome_labels_included": False,
    "packet_tree_sha256": digest(manifest),
}
(OUT / "PACKET_AUDIT.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
print(json.dumps(audit, indent=2, sort_keys=True))
