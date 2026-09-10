from scripts.validation_e1_detection_control import (
    all_to_any,
    classify,
    drop_effect,
    drop_required_conjunct,
    weaken_value_to_existence,
)


def test_drop_required_conjunct_ignores_only_target_field():
    diff = [{"path": "records[1].recipient", "result": "Bob", "golden": "Alice"}]
    assert drop_required_conjunct(diff, "recipient")
    assert not drop_required_conjunct(diff, "amount")


def test_weaken_value_to_existence_requires_present_target_and_no_other_diff():
    diff = [{"path": "records[1].recipient", "result": "Bob", "golden": "Alice"}]
    assert weaken_value_to_existence(diff, "recipient")
    assert not weaken_value_to_existence(diff, "amount")
    assert not weaken_value_to_existence([{"path": "records[1].recipient", "result": None}], "recipient")


def test_conjunction_mutants_accept_partial_completion():
    effects = {"send": True, "update": False}
    assert all_to_any(list(effects.values()))
    assert drop_effect(effects, "update")
    assert not drop_effect(effects, "send")


def test_detection_accounting_distinguishes_uncovered_and_survived():
    assert classify(True, True, True) == "KILLED"
    assert classify(True, True, False) == "SURVIVED"
    assert classify(True, False, False) == "UNCOVERED"
    assert classify(False, False, False) == "INVALID_MUTANT"
