"""The coordinator recognises registered agent capabilities and routes to them (offline: scripted
LLM, hash embeddings, in-memory vector store)."""
import json
from pathlib import Path

import pytest

from agents.contract import (AgentHandler, AgentManifest, AgentRegistry, AgentResult, Capability, InProcessAgent)
from agents.contract.base import AgentError
from agents.coordinator import handoff as H
from agents.coordinator import intent as I
from agents.coordinator.coordinator import Coordinator
from agents.coordinator.handoff import ModelHandoff
from agents.coordinator.intent import HeuristicIntentClassifier, LLMIntentClassifier
from agents.coordinator.projects.json_store import JsonProjectStore
from agents.coordinator.state.machine import StateMachine
from agents.paths import AGENTS_CONFIG, PROJECT_STATES_CONFIG
from agents.regulatory_docs.agent import RegulatoryDocsHandler
from agents.regulatory_docs.config import VectorizationConfig
from agents.regulatory_docs.library import DocumentLibrary
from agents.text_to_bpmn.pipeline.llm.mock import ScriptedLLMProvider
from agents.text_to_eflint.agent import TextToEflintHandler
from agents.text_to_eflint.generation.prompts import TASK_DRAFT

from .regulatory_docs.conftest import InMemoryStore, offline_embedder_factory, store_factory_for
from .text_to_eflint._eflint_samples import TEXT, spec

USER = "user@example.com"
REGULATION = "# Privacy Regulation\n\n## Scope\nThis regulation applies to all data controllers.\n"
CFG = {
    "default_profile": "offline",
    "profiles": {"offline": {"embedding": {"provider": "hash", "dimension": 16},
                             "qdrant": {"cluster": "memory", "collection": "regs"}}},
}


class SummarizeHandler(AgentHandler):
    """A newly registered agent the coordinator has no code for."""

    def __init__(self):
        self.requests = []

    def manifest(self):
        return AgentManifest(name="summarizer", capabilities=[
            Capability(intent="summarize_document", description="Summarize a document in plain language.")])

    def handle(self, request):
        self.requests.append(request)
        return AgentResult(agent="summarizer", intent=request.intent, output={"summary": "short version"},
                           artifacts_ref="run1")


@pytest.fixture
def coordinator(tmp_path):
    docs = RegulatoryDocsHandler(
        library=DocumentLibrary(tmp_path / "docs"),
        config=VectorizationConfig.model_validate(CFG),
        embedder_factory=offline_embedder_factory,
        store_factory=store_factory_for(InMemoryStore()),
    )
    eflint = TextToEflintHandler(provider=ScriptedLLMProvider({TASK_DRAFT: spec()}), runs_dir=tmp_path / "runs",
                                 documents_dir=tmp_path / "docs")
    intents = {i: "regulatory_docs" for i in (I.UPLOAD_DOCUMENT, I.FIND_DOCUMENTS, I.VECTORIZE_DOCUMENT,
                                              I.LIST_VECTORIZED, I.SEARCH_VECTORS)}
    intents.update({I.GENERATE_EFLINT: "text_to_eflint", I.VALIDATE_EFLINT: "text_to_eflint"})
    return Coordinator(
        registry=AgentRegistry(
            agents={"regulatory_docs": InProcessAgent(docs), "text_to_eflint": InProcessAgent(eflint)},
            intent_map=intents),
        store=JsonProjectStore(tmp_path / "projects"),
        machine=StateMachine.from_config(PROJECT_STATES_CONFIG),
        classifier=HeuristicIntentClassifier(),
        handoff=ModelHandoff(tmp_path / "handoff"),
    )


def upload(coord, text=REGULATION, title="Privacy Regulation") -> str:
    resp = coord.handle_message(USER, f'Upload this regulation titled "{title}"\n```regulation\n{text}```')
    assert resp.intent == I.UPLOAD_DOCUMENT and resp.agent_result.ok
    return resp.agent_result.output["document"]["doc_id"]


def project_with(coord):
    project = coord._create_project(USER, "Case handling", "A case is opened and closed.")
    coord.store.save(project)
    return project.id


# --- heuristic recognition ------------------------------------------------------------------------


@pytest.mark.parametrize("text, intent, slots", [
    ("Upload this regulation: Article 1 applies to everyone", I.UPLOAD_DOCUMENT, {}),
    ("please add a new regulation", I.UPLOAD_DOCUMENT, {}),
    ("upload gdpr.pdf", I.UPLOAD_DOCUMENT, {}),
    ("List the regulatory documents", I.FIND_DOCUMENTS, {}),
    ("which documents do we have?", I.FIND_DOCUMENTS, {}),
    ("Find documents about GDPR", I.FIND_DOCUMENTS, {"query": "GDPR"}),
    ("Vectorize the GDPR document", I.VECTORIZE_DOCUMENT, {"document_ref": "GDPR"}),
    ("vectorise document abc123def456 using the openai-large profile", I.VECTORIZE_DOCUMENT,
     {"document_ref": "abc123def456", "profile": "openai-large"}),
    ("Index the documents", I.VECTORIZE_DOCUMENT, {}),
    ("Which documents are vectorized?", I.LIST_VECTORIZED, {}),
    ("show what is in qdrant", I.LIST_VECTORIZED, {}),
    ("Search the regulations for data retention", I.SEARCH_VECTORS, {"query": "data retention"}),
    ("Search the regulations: data retention", I.SEARCH_VECTORS, {"query": "data retention"}),
    ("What does the GDPR say about consent?", I.SEARCH_VECTORS, {"query": "consent"}),
    ("semantic search data retention", I.SEARCH_VECTORS, {"query": "data retention"}),
    ("search the documents for compliance duties, top 3", I.SEARCH_VECTORS, {"query": "compliance duties", "limit": 3}),
    ("Generate an eFLINT specification from the GDPR document", I.GENERATE_EFLINT, {"document_ref": "GDPR"}),
    ("Formalise this regulation as eFLINT: all controllers must log access", I.GENERATE_EFLINT, {}),
    ("Validate the eFLINT specification", I.VALIDATE_EFLINT, {}),
    ("What can you do?", I.LIST_CAPABILITIES, {}),
    ("help", I.LIST_CAPABILITIES, {}),
])
def test_heuristic_recognizes_agent_commands(text, intent, slots):
    result = HeuristicIntentClassifier().classify(text)
    assert (result.intent, result.slots) == (intent, slots)


@pytest.mark.parametrize("text, intent", [
    # Still verification, not eFLINT authoring or document search.
    ("Verify the process against the eFLINT specification", I.VERIFY_EFLINT),
    ("Check that the Petri net complies with the regulation", I.VERIFY_EFLINT),
    ("Generate a Petri net and verify it against eFLINT", I.VERIFY_EFLINT),
    ("Make sure the process follows eFLINT norms and check it", I.VERIFY_EFLINT),
    ("Validate the process against the eFLINT specification", I.VERIFY_EFLINT),
    # Projects are not documents.
    ("list my projects", I.LIST_PROJECTS),
    ("find similar project about regulation", I.FIND_PROJECT),
    # A process that mentions documents and searching is a process.
    ("The clerk uploads the invoice, searches the regulations for the applicable rule, and the manager "
     "then approves the document before the case is finally closed by the back office.", I.ENGINEER_PROCESS),
    ("Upload the invoice to the portal and notify the manager", I.ENGINEER_PROCESS),
])
def test_other_requests_are_not_taken_for_agent_commands(text, intent):
    assert HeuristicIntentClassifier().classify(text).intent == intent


def test_attached_document_means_upload_and_is_not_part_of_the_prose():
    prose, found = I.extract_attachments(f"Add this\n```regulation\n{REGULATION}```")
    assert prose == "Add this" and found == {"document": REGULATION}
    assert HeuristicIntentClassifier().classify(prose, context={"attachments": ["document"]}).intent == I.UPLOAD_DOCUMENT


@pytest.mark.parametrize("ref, cleaned", [
    ("the GDPR document", "GDPR"), ("this regulation", ""), ("the document", ""),
    ("General Data Protection Regulation", "General Data Protection"), ("GDPR in the library", "GDPR"),
])
def test_clean_document_ref(ref, cleaned):
    assert I.clean_document_ref(ref) == cleaned


# --- regulatory documents -------------------------------------------------------------------------


def test_upload_vectorize_list_and_search_end_to_end(coordinator):
    doc_id = upload(coordinator)

    resp = coordinator.handle_message(USER, "Vectorize the Privacy Regulation document")
    assert resp.intent == I.VECTORIZE_DOCUMENT and resp.agent_result.ok and "'regs'" in resp.reply
    assert resp.agent_result.artifacts_ref == doc_id

    resp = coordinator.handle_message(USER, "Which documents are vectorized?")
    assert resp.intent == I.LIST_VECTORIZED and "Privacy Regulation" in resp.reply

    resp = coordinator.handle_message(USER, "Search the regulations for data controllers")
    assert resp.intent == I.SEARCH_VECTORS and "applies to all data controllers" in resp.reply

    resp = coordinator.handle_message(USER, "List the regulatory documents")
    assert resp.intent == I.FIND_DOCUMENTS and doc_id in resp.reply and "vectorized" in resp.reply


def test_upload_never_reads_a_server_path(coordinator):
    resp = coordinator.handle_message(USER, "upload /etc/hostname.txt")
    assert resp.intent == I.UPLOAD_DOCUMENT and resp.needs_input and resp.agent_result is None
    assert "demo app" in resp.reply
    assert coordinator.handle_message(USER, "list the documents").reply == "The document library is empty."


def test_vectorize_ambiguous_or_missing_document_asks(coordinator):
    assert "empty" in coordinator.handle_message(USER, "vectorize the document").reply
    upload(coordinator, title="Privacy A")
    upload(coordinator, REGULATION + "More.\n", title="Privacy B")

    resp = coordinator.handle_message(USER, "vectorize the privacy document")
    assert resp.needs_input and resp.reply.startswith("Which document") and "Privacy B" in resp.reply

    resp = coordinator.handle_message(USER, "vectorize 'Privacy A'")
    assert resp.agent_result.intent == I.VECTORIZE_DOCUMENT and resp.agent_result.ok

    resp = coordinator.handle_message(USER, "vectorize the tax document")
    assert resp.needs_input and "No document in the library matches 'tax'" in resp.reply


def test_search_without_a_topic_or_hits_explains(coordinator):
    resp = coordinator.handle_message(USER, "search the regulations")
    assert resp.needs_input and resp.agent_result is None
    assert "Has a document been vectorized" in coordinator.handle_message(USER, "search the laws for x").reply


def test_unknown_profile_is_reported_not_raised(coordinator):
    upload(coordinator)
    resp = coordinator.handle_message(USER, "vectorize the document with profile nope")
    assert resp.needs_input and not resp.agent_result.ok and "could not be vectorized" in resp.reply


def test_upload_from_a_project_is_noted_in_its_history(coordinator):
    pid = project_with(coordinator)
    resp = coordinator.handle_message(USER, "Upload this regulation: " + TEXT, project_id=pid)
    assert resp.project.id == pid
    project = coordinator.store.get(USER, pid)
    assert project.state == resp.state and project.history[-1].intent == I.UPLOAD_DOCUMENT


# --- eFLINT authoring -----------------------------------------------------------------------------


def test_generated_spec_becomes_the_projects_spec_for_verification(coordinator):
    pid = project_with(coordinator)
    state = coordinator.store.get(USER, pid).state

    resp = coordinator.handle_message(USER, "Generate eFLINT: " + TEXT, project_id=pid)

    assert resp.intent == I.GENERATE_EFLINT and resp.agent_result.ok and resp.needs_input is False
    assert "ready for eFLINT verification" in resp.reply
    stored = coordinator.handoff.load(pid, H.EFLINT)
    assert stored == resp.agent_result.output["files"]["spec.eflint"]
    project = coordinator.store.get(USER, pid)
    assert project.state == state and resp.agent_result.artifacts_ref in project.runs

    resp = coordinator.handle_message(USER, "validate the eflint specification", project_id=pid)
    assert resp.intent == I.VALIDATE_EFLINT and resp.reply.startswith("The project's eFLINT specification is valid")


def test_spec_that_needs_review_does_not_replace_the_stored_one(coordinator, monkeypatch):
    pid = project_with(coordinator)
    coordinator.handoff.save(pid, H.EFLINT, "Fact good.\n")
    agent = coordinator.registry.get_for_intent(I.GENERATE_EFLINT)
    monkeypatch.setattr(agent, "invoke", lambda request: AgentResult(
        agent="text_to_eflint", intent=request.intent, output={
            "status": "needs_review", "summary": {"errors": 1}, "files": {"spec.eflint": "Fact bad.\n"},
            "findings": [{"severity": "error", "message": "undeclared type"}]}))

    resp = coordinator.handle_message(USER, "Generate eFLINT: " + TEXT, project_id=pid)

    assert resp.needs_input and "did not store it" in resp.reply and "undeclared type" in resp.reply
    assert coordinator.handoff.load(pid, H.EFLINT) == "Fact good.\n"


def test_generate_from_a_library_document(coordinator, monkeypatch):
    doc_id = upload(coordinator, TEXT, title="Data rules")
    agent = coordinator.registry.get_for_intent(I.GENERATE_EFLINT)
    requests, invoke = [], agent.invoke
    monkeypatch.setattr(agent, "invoke", lambda request: requests.append(request) or invoke(request))

    resp = coordinator.handle_message(USER, "Generate eFLINT from the Data rules document")

    assert resp.agent_result.ok and "from 'Data rules'" in resp.reply
    assert requests[0].payload["document_id"] == doc_id and "text" not in requests[0].payload


def test_generate_without_a_source_asks(coordinator):
    resp = coordinator.handle_message(USER, "generate eflint")
    assert resp.intent == I.GENERATE_EFLINT and resp.needs_input and resp.agent_result is None


def test_generation_without_llm_is_reported(coordinator, monkeypatch):
    agent = coordinator.registry.get_for_intent(I.GENERATE_EFLINT)
    monkeypatch.setattr(agent, "invoke", lambda request: AgentResult(
        agent="text_to_eflint", intent=request.intent, status="failed", error="LLMError: no key"))
    resp = coordinator.handle_message(USER, "Generate eFLINT: " + TEXT)
    assert resp.needs_input and "could not be generated" in resp.reply and "no key" in resp.reply


def test_agents_that_are_not_wired_are_reported(tmp_path):
    coord = Coordinator(registry=AgentRegistry(agents={}, intent_map={}), store=JsonProjectStore(tmp_path),
                        machine=StateMachine.from_config(PROJECT_STATES_CONFIG))
    assert "not available" in coord.handle_message(USER, "search the laws for x").reply
    assert "not available" in coord.handle_message(USER, "Generate eFLINT: all parties must log").reply


# --- discovery of newly registered agents ---------------------------------------------------------


def registry_with_new_agent():
    handler = SummarizeHandler()
    return handler, AgentRegistry(agents={"summarizer": InProcessAgent(handler)},
                                  intent_map={"summarize_document": "summarizer"})


def test_registry_describes_capabilities_from_the_manifest():
    _, registry = registry_with_new_agent()
    [cap] = registry.capabilities()
    assert (cap.agent, cap.intent, cap.description) == (
        "summarizer", "summarize_document", "Summarize a document in plain language.")


def test_registry_falls_back_to_config_when_a_remote_manifest_is_unreachable():
    class Down(InProcessAgent):
        def __init__(self):
            self.name = "remote"

        def manifest(self):
            raise AgentError("down")

    registry = AgentRegistry(agents={"remote": Down()}, intent_map={"do_thing": "remote"},
                             descriptions={"remote": "Does the thing."})
    [cap] = registry.capabilities()
    assert (cap.intent, cap.description) == ("do_thing", "Does the thing.")


def test_every_configured_intent_is_recognised_by_the_coordinator():
    config = json.loads(Path(AGENTS_CONFIG).read_text(encoding="utf-8"))
    for entry in config["agents"]:
        for intent in entry["intents"]:
            assert intent in I.INTENTS and intent in I._GUIDANCE, intent


def test_built_coordinator_registers_every_agent(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from agents.coordinator.factory import build_coordinator

    coord = build_coordinator(store=JsonProjectStore(tmp_path))
    assert set(coord.registry.names()) == {"text_to_bpmn", "regulatory_docs", "petri_net_generator",
                                           "text_to_eflint", "eflint_verifier"}
    reply = coord.handle_message(USER, "what can you do?").reply
    assert "search vectors (regulatory_docs)" in reply and "verify eflint (eflint_verifier)" in reply


def test_llm_classifier_learns_new_capabilities_from_the_registry():
    _, registry = registry_with_new_agent()
    provider = ScriptedLLMProvider({"intent_classification": {"intent": "summarize_document", "query": "x"}})
    result = LLMIntentClassifier(provider, capabilities=registry.capabilities()).classify("shorten the GDPR")

    assert result.intent == "summarize_document" and result.slots == {"query": "x"}
    call = provider.calls[-1]
    assert "summarize_document: Summarize a document in plain language." in call.system
    enum = call.schema["properties"]["intent"]["enum"]
    # Agents that are not available cannot be chosen; what the coordinator answers itself can.
    assert "summarize_document" in enum and I.LIST_PROJECTS in enum and I.UNKNOWN in enum
    assert I.SEARCH_VECTORS not in enum and I.GENERATE_PETRI_NET not in enum


def test_llm_classifier_without_capabilities_offers_every_known_intent():
    provider = ScriptedLLMProvider({"intent_classification": {"intent": I.UNKNOWN}})
    LLMIntentClassifier(provider).classify("hello")
    assert set(provider.calls[-1].schema["properties"]["intent"]["enum"]) == set(I.INTENTS)


def test_new_agent_is_reached_without_a_dedicated_route(tmp_path):
    handler, registry = registry_with_new_agent()
    coord = Coordinator(registry=registry, store=JsonProjectStore(tmp_path),
                        machine=StateMachine.from_config(PROJECT_STATES_CONFIG),
                        classifier=HeuristicIntentClassifier(registered_intents=registry.intents()))
    project = coord._create_project(USER, "P", "d")
    coord.store.save(project)

    resp = coord.handle_message(USER, "Please summarize document on privacy", project_id=project.id)

    assert resp.intent == "summarize_document" and resp.reply == "summarizer completed 'summarize document': short version"
    assert handler.requests[0].payload == {"text": "Please summarize document on privacy"}
    saved = coord.store.get(USER, project.id)
    assert "run1" in saved.runs and saved.state == project.state and saved.history[-1].agent == "summarizer"


def test_unknown_message_offers_the_registered_capabilities(tmp_path):
    _, registry = registry_with_new_agent()
    coord = Coordinator(registry=registry, store=JsonProjectStore(tmp_path),
                        machine=StateMachine.from_config(PROJECT_STATES_CONFIG))
    resp = coord.handle_message(USER, "hello")
    assert resp.intent == I.UNKNOWN and resp.needs_input and "summarize document (summarizer)" in resp.reply
