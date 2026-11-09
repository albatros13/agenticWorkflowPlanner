"""Regression tests for the coordinator fixes in docs/review/CODE-REVIEW-FIX.md (B3-B11, B15)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agents.contract import AgentRegistry, InProcessAgent
from agents.contract.base import AgentHandler
from agents.contract.models import AgentManifest, AgentRequest, AgentResult, Capability, JobStatus
from agents.contract.registry import RegistryError
from agents.coordinator import intent as I
from agents.coordinator.cache import build_cache
from agents.coordinator.coordinator import Coordinator, _derive_name
from agents.coordinator.handoff import ModelHandoff
from agents.coordinator.intent import HeuristicIntentClassifier, LLMIntentClassifier
from agents.coordinator.projects.json_store import JsonProjectStore
from agents.coordinator.projects.store import ProjectNotFoundError
from agents.coordinator.state.machine import StateMachine
from agents.paths import PROJECT_STATES_CONFIG
from agents.text_to_bpmn.pipeline.llm.base import LLMError
from agents.text_to_bpmn.pipeline.llm.mock import ScriptedLLMProvider

USER = "u@x.com"


def coordinator(tmp_path, agents: dict | None = None, intent_map: dict | None = None) -> Coordinator:
    return Coordinator(
        registry=AgentRegistry(agents=agents or {}, intent_map=intent_map or {}),
        store=JsonProjectStore(tmp_path / "projects"),
        machine=StateMachine.from_config(PROJECT_STATES_CONFIG),
        classifier=HeuristicIntentClassifier(),
        handoff=ModelHandoff(tmp_path / "handoff"),
    )


# --- B3: process descriptions containing command words -------------------------------------------


@pytest.mark.parametrize("text", [
    "The specialist reviews the claim and approves the payment to the customer.",
    "The clerk records the progress of the application and notifies the applicant by email.",
    "At checkout the customer pays; the shop must follow consumer regulations on refunds.",
    "The manager may delete the draft order if the customer cancels before shipping.",
    "The officer must verify the identity of the applicant, and then the clerk lists all open "
    "requests, checks their status and removes the duplicates before the weekly report is sent.",
])
def test_process_descriptions_are_not_taken_for_commands(text):
    assert HeuristicIntentClassifier().classify(text).intent == I.ENGINEER_PROCESS


@pytest.mark.parametrize("text, intent", [
    ("delete this project", I.DELETE_PROJECT),
    ("remove project", I.DELETE_PROJECT),
    ("please delete the project", I.DELETE_PROJECT),
    ("list my projects", I.LIST_PROJECTS),
    ("show all projects", I.LIST_PROJECTS),
    ("status", I.PROJECT_STATUS),
    ("what is the status?", I.PROJECT_STATUS),
    ("project status", I.PROJECT_STATUS),
    ("Convert it to a Petrinet", I.GENERATE_PETRI_NET),
])
def test_short_commands_still_work(text, intent):
    assert HeuristicIntentClassifier().classify(text).intent == intent


def test_a_description_mentioning_delete_does_not_delete_the_open_project(tmp_path):
    c = coordinator(tmp_path)
    project = c._create_project(USER, "Orders", "Order handling")
    resp = c.handle_message(USER, "The manager may delete the draft order if the customer cancels.",
                            project_id=project.id)
    assert resp.intent != I.DELETE_PROJECT
    assert c.store.get(USER, project.id).id == project.id


# --- B9: the LLM classifier's answer is untrusted ------------------------------------------------


def test_llm_intent_outside_the_offered_set_becomes_unknown():
    provider = ScriptedLLMProvider({"intent_classification": {"intent": "format_disk"}})
    assert LLMIntentClassifier(provider).classify("hello").intent == I.UNKNOWN


def test_llm_slots_of_the_wrong_type_are_dropped_or_coerced():
    provider = ScriptedLLMProvider({"intent_classification": {
        "intent": I.VERIFY_EFLINT, "max_states": "12", "limit": "many", "semantics_changed": "yes",
        "query": 5, "title": "T"}})
    result = LLMIntentClassifier(provider).classify("verify")
    assert result.slots == {"max_states": 12, "title": "T"}


def test_llm_failure_falls_back_to_the_heuristic_classifier():
    class Broken(ScriptedLLMProvider):
        def generate_json(self, **kwargs):
            raise LLMError("rate limited")

    assert LLMIntentClassifier(Broken()).classify("list my projects").intent == I.LIST_PROJECTS


# --- B4/B6: the factory loads .env before choosing the classifier ---------------------------------


def test_factory_reads_the_key_from_dotenv(monkeypatch):
    from agents.coordinator import factory

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(factory, "load_dotenv", lambda: monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env"))
    monkeypatch.setattr(factory, "default_provider", lambda: ScriptedLLMProvider())
    registry = AgentRegistry(agents={}, intent_map={})
    assert isinstance(factory._default_classifier(registry), LLMIntentClassifier)


def test_factory_logs_and_falls_back_when_the_llm_classifier_cannot_be_built(monkeypatch, caplog):
    from agents.coordinator import factory

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(factory, "load_dotenv", lambda: None)

    def boom():
        raise LLMError("no SDK")

    monkeypatch.setattr(factory, "default_provider", boom)
    classifier = factory._default_classifier(AgentRegistry(agents={}, intent_map={}))
    assert isinstance(classifier, HeuristicIntentClassifier)
    assert "LLM intent classifier unavailable" in caplog.text


# --- B5/B14: ids are validated, never rewritten ---------------------------------------------------


@pytest.mark.parametrize("user_id", ["..", ".", "a/b", "../x", ".hidden", ""])
def test_unsafe_user_ids_are_rejected(tmp_path, user_id):
    store = JsonProjectStore(tmp_path / "projects")
    with pytest.raises(ValueError):
        store.list(user_id)


def test_distinct_user_ids_no_longer_collide(tmp_path):
    c = coordinator(tmp_path)
    c._create_project("a_b", "P", "d")
    with pytest.raises(ValueError):
        c.store.list("a/b")  # used to map onto a_b's folder
    assert len(c.store.list("a_b")) == 1


def test_reads_do_not_create_user_folders(tmp_path):
    store = JsonProjectStore(tmp_path / "projects")
    assert store.list("nobody") == []
    with pytest.raises(ProjectNotFoundError):
        store.get("nobody", "abc")
    assert not (tmp_path / "projects" / "nobody").exists()


def test_unsafe_project_id_is_not_found(tmp_path):
    with pytest.raises(ProjectNotFoundError):
        JsonProjectStore(tmp_path / "projects").get(USER, "../../etc/passwd")


def test_store_search_matches_name_and_description(tmp_path):
    c = coordinator(tmp_path)
    c._create_project(USER, "Loans", "Credit approval")
    c._create_project(USER, "Orders", "Shipping")
    assert [r.name for r in c.store.search(USER, "credit")] == ["Loans"]


def test_handoff_rejects_unsafe_project_ids(tmp_path):
    handoff = ModelHandoff(tmp_path / "handoff")
    victim = tmp_path / "keep"
    victim.mkdir()
    with pytest.raises(ValueError):
        handoff.remove("../keep")
    with pytest.raises(ValueError):
        handoff.save("..", "model.bpmn", "<x/>")
    assert victim.exists()


def test_service_rejects_unsafe_user_ids(tmp_path, monkeypatch):
    import agents.coordinator.service as svc

    monkeypatch.setattr(svc, "_coordinator", coordinator(tmp_path))
    client = TestClient(svc.app)
    assert client.get("/coordinator/projects", params={"user_id": ".."}).status_code == 400
    assert client.post("/coordinator/message", json={"text": "list my projects", "user_id": "../x"}).status_code == 400


def test_service_delete_uses_the_coordinator(tmp_path, monkeypatch):
    import agents.coordinator.service as svc

    c = coordinator(tmp_path)
    project = c._create_project(USER, "P", "d")
    c.handoff.save_bpmn(project.id, "<definitions/>")
    monkeypatch.setattr(svc, "_coordinator", c)
    client = TestClient(svc.app)
    assert client.delete(f"/coordinator/projects/{project.id}", params={"user_id": USER}).json() == {"deleted": project.id}
    assert c.handoff.load_bpmn(project.id) is None
    assert client.delete(f"/coordinator/projects/{project.id}", params={"user_id": USER}).status_code == 404


# --- B7/B8: implicit projects are persisted; an unknown project id is reported ---------------------


def test_implicit_project_is_saved_even_when_no_bpmn_agent_is_available(tmp_path):
    c = coordinator(tmp_path)  # no text_to_bpmn agent registered
    resp = c.handle_message(USER, "The clerk registers the claim and then the manager approves it.")
    assert resp.intent == I.ENGINEER_PROCESS and "not available" in resp.reply
    assert c.store.get(USER, resp.project.id).state == resp.state == "DESCRIPTION_DRAFTED"


def test_unknown_project_id_is_reported_not_replaced(tmp_path):
    c = coordinator(tmp_path)
    resp = c.handle_message(USER, "The clerk registers the claim and then the manager approves it.",
                            project_id="doesnotexist")
    assert resp.needs_input and "couldn't find that project" in resp.reply
    assert c.store.list(USER) == []


def test_derive_name_accepts_none():
    assert _derive_name(None) == "Untitled project"
    assert _derive_name("# Title\nbody") == "Title"


# --- B10: a registered verify_model agent drives the lifecycle ------------------------------------


class _Verifier(AgentHandler):
    def __init__(self, verdict):
        self.verdict, self.requests = verdict, []

    def manifest(self) -> AgentManifest:
        return AgentManifest(name="verifier", capabilities=[Capability(intent=I.VERIFY_MODEL)])

    def handle(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        return AgentResult(agent="verifier", intent=request.intent, output={"verdict": self.verdict})


@pytest.mark.parametrize("verdict, state", [("passed", "VERIFIED"), ("failed", "VERIFICATION_FAILED")])
def test_verify_model_agent_drives_the_lifecycle(tmp_path, verdict, state):
    handler = _Verifier(verdict)
    c = coordinator(tmp_path, {"verifier": InProcessAgent(handler)}, {I.VERIFY_MODEL: "verifier"})
    project = c._create_project(USER, "P", "d")
    project.state = "MODEL_READY_FOR_VERIFICATION"
    c.store.save(project)
    c.handoff.save_bpmn(project.id, "<definitions/>")

    resp = c.handle_message(USER, "please verify the model", project_id=project.id)
    assert resp.state == state == c.store.get(USER, project.id).state
    assert handler.requests[0].payload["bpmn_xml"] == "<definitions/>"


def test_inconclusive_verification_keeps_the_state(tmp_path):
    c = coordinator(tmp_path, {"verifier": InProcessAgent(_Verifier(None))}, {I.VERIFY_MODEL: "verifier"})
    project = c._create_project(USER, "P", "d")
    project.state = "MODEL_READY_FOR_VERIFICATION"
    c.store.save(project)
    resp = c.handle_message(USER, "please verify the model", project_id=project.id)
    assert resp.state == "MODEL_READY_FOR_VERIFICATION" and "inconclusive" in resp.reply


# --- B15 + registry ------------------------------------------------------------------------------


def test_unimplemented_qdrant_cache_is_refused_at_start_up():
    with pytest.raises(ValueError, match="not implemented"):
        build_cache("qdrant")


def test_an_intent_claimed_by_two_agents_is_a_config_error(tmp_path):
    config = tmp_path / "agents.json"
    config.write_text('{"agents": [{"name": "a", "intents": ["x"]}, {"name": "b", "intents": ["x"]}]}')
    with pytest.raises(RegistryError, match="claimed by both"):
        AgentRegistry.from_config(config)


def test_route_registered_without_a_loaded_agent_answers_politely(tmp_path):
    c = Coordinator(
        registry=AgentRegistry(agents={}, intent_map={"summarize_document": "summarizer"}),
        store=JsonProjectStore(tmp_path / "projects"),
        machine=StateMachine.from_config(PROJECT_STATES_CONFIG),
        classifier=HeuristicIntentClassifier(registered_intents=["summarize_document"]),
    )
    resp = c.handle_message(USER, "summarize document on privacy")
    assert resp.needs_input and "No agent is available" in resp.reply


def test_failed_agent_result_is_recorded_in_the_project(tmp_path):
    class Failing(_Verifier):
        def handle(self, request):
            return AgentResult(agent="verifier", intent=request.intent, status=JobStatus.failed, error="down")

    c = coordinator(tmp_path, {"verifier": InProcessAgent(Failing(None))}, {I.VERIFY_MODEL: "verifier"})
    project = c._create_project(USER, "P", "d")
    project.state = "MODEL_READY_FOR_VERIFICATION"
    c.store.save(project)
    resp = c.handle_message(USER, "please verify the model", project_id=project.id)
    assert "could not be completed: down" in resp.reply
    assert c.store.get(USER, project.id).history[-1].summary.endswith("down")
