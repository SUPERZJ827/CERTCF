import pytest

pytest.importorskip("agentdojo")

from scripts.phase1_3_agentdojo_common import call_data, canonical_bytes
from scripts.phase1_3_retry1_adapter import persisted_call_to_runtime


def test_persisted_arguments_round_trip_to_runtime_args() -> None:
    persisted = {
        "function": "example_tool",
        "arguments": {"nested": {"value": 7}, "items": ["a", "b"]},
        "id": "call-1",
        "placeholder_args": None,
    }

    runtime_call = persisted_call_to_runtime(persisted)
    round_tripped = call_data(runtime_call)

    assert runtime_call.function == persisted["function"]
    assert canonical_bytes(runtime_call.args) == canonical_bytes(persisted["arguments"])
    assert canonical_bytes(round_tripped) == canonical_bytes(persisted)
