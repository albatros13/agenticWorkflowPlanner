"""Project lifecycle state machine (config/project-states.json)."""
import pytest

from agents.coordinator.state.machine import StateMachine, StateMachineError
from agents.paths import PROJECT_STATES_CONFIG


@pytest.fixture
def machine() -> StateMachine:
    return StateMachine.from_config(PROJECT_STATES_CONFIG)


def test_initial_state_and_states(machine):
    assert machine.initial_state == "INTENT_CAPTURED"
    assert "VERIFIED" in machine.states()
    assert "RETIRED" in machine.states()


def test_global_actions_merged_into_every_state(machine):
    for state in machine.states():
        actions = machine.allowed_actions(state)
        assert "reviseDescription" in actions
        assert "deleteProject" in actions


def test_happy_path_transitions(machine):
    assert machine.apply("INTENT_CAPTURED", "createDescription").to_state == "DESCRIPTION_DRAFTED"
    assert machine.apply("DESCRIPTION_DRAFTED", "createModel").to_state == "MODEL_IN_PROGRESS"
    assert machine.apply("MODEL_IN_PROGRESS", "refineModel").to_state == "MODEL_IN_PROGRESS"
    assert machine.apply("MODEL_IN_PROGRESS", "markModelReadyForVerification").to_state == "MODEL_READY_FOR_VERIFICATION"
    assert machine.apply("MODEL_READY_FOR_VERIFICATION", "startVerification").to_state == "VERIFICATION_IN_PROGRESS"
    assert machine.apply("VERIFICATION_IN_PROGRESS", "completeVerificationSuccess").to_state == "VERIFIED"


def test_disallowed_action_raises(machine):
    with pytest.raises(StateMachineError):
        machine.apply("INTENT_CAPTURED", "deploy")
    with pytest.raises(StateMachineError):
        machine.apply("VERIFIED", "createModel")


def test_revise_description_without_semantic_change_stays(machine):
    tr = machine.apply("VERIFIED", "reviseDescription", semantics_changed=False)
    assert tr.to_state == "VERIFIED"
    assert tr.invalidated is False


def test_revise_description_with_semantic_change_invalidates(machine):
    tr = machine.apply("VERIFIED", "reviseDescription", semantics_changed=True)
    assert tr.to_state == "MODEL_IN_PROGRESS"
    assert tr.invalidated is True


def test_delete_is_terminal_from_any_state(machine):
    for state in machine.states():
        tr = machine.apply(state, "deleteProject")
        assert tr.terminal is True


def test_unknown_state_raises(machine):
    with pytest.raises(StateMachineError):
        machine.allowed_actions("NOPE")
