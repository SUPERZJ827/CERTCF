from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


SCRIPT = Path(__file__).parents[1] / "scripts" / "phase0_7_appworld_dual_replay.py"
SPEC = importlib.util.spec_from_file_location("phase0_7_dual_replay", SCRIPT)
assert SPEC and SPEC.loader
phase07 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = phase07
SPEC.loader.exec_module(phase07)


def test_identical_final_states_are_certified():
    status, reasons = phase07.classify_validity(
        original_initial_digest="start", transformed_initial_digest="start",
        original_execution_ok=True, transformed_execution_ok=True,
        calls_match=True, final_state_match=True, returned_value_match=True,
    )
    assert status == phase07.VALIDITY_CERTIFIED
    assert reasons == []


def test_changed_final_state_is_failed():
    status, _ = phase07.classify_validity(
        original_initial_digest="start", transformed_initial_digest="start",
        original_execution_ok=True, transformed_execution_ok=True,
        calls_match=True, final_state_match=False, returned_value_match=True,
    )
    assert status == phase07.VALIDITY_FAILED


def test_unavailable_state_is_unknown():
    status, _ = phase07.classify_validity(
        original_initial_digest="start", transformed_initial_digest="start",
        original_execution_ok=True, transformed_execution_ok=True,
        calls_match=True, final_state_match=None, returned_value_match=True,
    )
    assert status == phase07.VALIDITY_UNKNOWN


def test_transformed_execution_error_is_not_certified():
    status, _ = phase07.classify_validity(
        original_initial_digest="start", transformed_initial_digest="start",
        original_execution_ok=True, transformed_execution_ok=False,
        calls_match=True, final_state_match=True, returned_value_match=True,
    )
    assert status == phase07.VALIDITY_UNKNOWN


def test_evaluator_comparison_is_blocked_without_validity():
    assert phase07.evaluator_relation(phase07.VALIDITY_UNKNOWN, {"success": True}, {"success": False}) == "INVALID_FOR_EVALUATOR_TEST"


def test_initial_digest_mismatch_fails():
    status, _ = phase07.classify_validity(
        original_initial_digest="one", transformed_initial_digest="two",
        original_execution_ok=True, transformed_execution_ok=True,
        calls_match=True, final_state_match=True, returned_value_match=True,
    )
    assert status == phase07.VALIDITY_FAILED


def test_queue_selection_is_deterministic():
    records = [
        {"task_id": "b", "i": 2, "j": 3, "status": "POTENTIAL_CANDIDATE"},
        {"task_id": "a", "i": 1, "j": 4, "status": "POTENTIAL_CANDIDATE"},
        {"task_id": "skip", "i": 1, "j": 2, "status": "REJECTED"},
    ]
    first, first_digest = phase07.select_pilot_queue(records)
    second, second_digest = phase07.select_pilot_queue(list(reversed(records)))
    assert first == second
    assert first_digest == second_digest
