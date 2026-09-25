"""Coordinator end-to-end: intent -> state-gated action -> route -> persist (offline)."""
import pytest

from agents.contract import AgentRegistry
from agents.coordinator import intent as I
from agents.coordinator.cache import NoOpSemanticCache
from agents.coordinator.coordinator import Coordinator
from agents.coordinator.intent import HeuristicIntentClassifier
from agents.coordinator.projects.json_store import JsonProjectStore
from agents.coordinator.state.machine import StateMachine
from agents.paths import PROJECT_STATES_CONFIG
from agents.text_to_bpmn.agent import TextToBpmnHandler

from ._bpmn_fixtures import TEXT, scripted_provider

USER = "natallia@example.com"


@pytest.fixture
def coordinator(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    handler = TextToBpmnHandler(provider=scripted_provider())
    registry = AgentRegistry(
        agents={"text_to_bpmn": _in_process(handler)},
        intent_map={"engineer_process": "text_to_bpmn"},
    )
    return Coordinator(
        registry=registry,
        store=JsonProjectStore(tmp_path / "projects"),
        machine=StateMachine.from_config(PROJECT_STATES_CONFIG),
        cache=NoOpSemanticCache(),
        classifier=HeuristicIntentClassifier(),
    )


def _in_process(handler):
    from agents.contract import InProcessAgent

    return InProcessAgent(handler)


def test_engineer_creates_project_and_generates_model(coordinator):
    resp = coordinator.handle_message(USER, TEXT)
    assert resp.intent == I.ENGINEER_PROCESS
    assert resp.agent_result is not None and resp.agent_result.ok
    # A free-text description with no existing project -> new project, model generated.
    assert resp.state == "MODEL_IN_PROGRESS"
    assert resp.project is not None
    # The run id was attached to the project.
    project = coordinator.store.get(USER, resp.project.id)
    assert project.runs and project.runs[0] == resp.agent_result.artifacts_ref
    # Allowed actions reflect the new state (constrained response).
    assert "markModelReadyForVerification" in resp.allowed_actions


def test_refine_keeps_state_in_model_in_progress(coordinator):
    first = coordinator.handle_message(USER, TEXT)
    pid = first.project.id
    second = coordinator.handle_message(USER, "Please refine the workflow model.", project_id=pid)
    assert second.intent == I.ENGINEER_PROCESS
    assert second.state == "MODEL_IN_PROGRESS"
    project = coordinator.store.get(USER, pid)
    assert len(project.runs) == 2  # two generation runs recorded


def test_engineer_refused_in_verified_state(coordinator):
    resp = coordinator.handle_message(USER, TEXT)
    pid = resp.project.id
    # Drive the project to VERIFIED directly through the store + machine.
    project = coordinator.store.get(USER, pid)
    for action in ("markModelReadyForVerification", "startVerification", "completeVerificationSuccess"):
        project.state = coordinator.machine.apply(project.state, action).to_state
    coordinator.store.save(project)
    assert project.state == "VERIFIED"

    refused = coordinator.handle_message(USER, "generate the bpmn again", project_id=pid)
    assert refused.needs_input is True
    assert "isn't applicable" in refused.reply
    assert coordinator.store.get(USER, pid).state == "VERIFIED"  # unchanged


def test_list_and_delete(coordinator):
    r = coordinator.handle_message(USER, TEXT)
    pid = r.project.id
    listing = coordinator.handle_message(USER, "list my projects")
    assert listing.intent == I.LIST_PROJECTS and "1 project" in listing.reply

    deleted = coordinator.handle_message(USER, "delete this project", project_id=pid)
    assert deleted.intent == I.DELETE_PROJECT
    assert coordinator.store.list(USER) == []


def test_revise_description_semantic_change_from_verified(coordinator):
    resp = coordinator.handle_message(USER, TEXT)
    pid = resp.project.id
    project = coordinator.store.get(USER, pid)
    for action in ("markModelReadyForVerification", "startVerification", "completeVerificationSuccess"):
        project.state = coordinator.machine.apply(project.state, action).to_state
    coordinator.store.save(project)

    revised = coordinator.handle_message(
        USER,
        "revise description: add a new actor, the oncologist, changing the goal",
        project_id=pid,
    )
    assert revised.intent == I.REVISE_DESCRIPTION
    assert revised.state == "MODEL_IN_PROGRESS"  # semantic change invalidated verification
    assert "invalidated" in revised.reply


def test_verify_without_agent_is_refused_gracefully(coordinator):
    resp = coordinator.handle_message(USER, TEXT)
    pid = resp.project.id
    project = coordinator.store.get(USER, pid)
    project.state = coordinator.machine.apply(project.state, "markModelReadyForVerification").to_state
    coordinator.store.save(project)

    verify = coordinator.handle_message(USER, "please verify the model", project_id=pid)
    assert verify.intent == I.VERIFY_MODEL
    assert verify.needs_input is True
    assert "verification agent" in verify.reply.lower()
