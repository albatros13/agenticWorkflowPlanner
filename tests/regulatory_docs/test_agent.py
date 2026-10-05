"""End-to-end agent flow through the shared contract — offline (hash + in-memory store)."""
import pytest

from agents.contract import AgentRegistry, AgentRequest
from agents.regulatory_docs.agent import (
    AGENT_NAME,
    FIND_DOCUMENTS,
    UPLOAD_DOCUMENT,
    VECTORIZE_DOCUMENT,
    RegulatoryDocsHandler,
)
from agents.regulatory_docs.config import VectorizationConfig
from agents.regulatory_docs.library import DocumentLibrary

from .conftest import InMemoryStore, offline_embedder_factory, store_factory_for

MD = "# Privacy Reg\n\n## Scope\nThis regulation applies to all controllers.\n"

CFG = {
    "default_profile": "offline",
    "profiles": {"offline": {"embedding": {"provider": "hash", "dimension": 16},
                             "qdrant": {"cluster": "memory", "collection": "regs"}}},
}


def _handler(tmp_path, store=None):
    return RegulatoryDocsHandler(
        library=DocumentLibrary(tmp_path),
        config=VectorizationConfig.model_validate(CFG),
        embedder_factory=offline_embedder_factory,
        store_factory=store_factory_for(store or InMemoryStore()),
    )


def test_manifest_serves_all_intents(tmp_path):
    m = _handler(tmp_path).manifest()
    assert m.name == AGENT_NAME
    assert m.serves(UPLOAD_DOCUMENT) and m.serves(FIND_DOCUMENTS) and m.serves(VECTORIZE_DOCUMENT)


def test_upload_find_vectorize_flow(tmp_path):
    store = InMemoryStore()
    h = _handler(tmp_path, store)

    up = h.handle(AgentRequest(intent=UPLOAD_DOCUMENT, payload={"text": MD, "title": "Privacy Reg"}))
    assert up.ok, up.error
    doc_id = up.output["document"]["doc_id"]
    assert up.output["profiles"]  # vectorization options surfaced for selection

    found = h.handle(AgentRequest(intent=FIND_DOCUMENTS, payload={"query": "privacy"}))
    assert found.ok and found.output["count"] == 1
    assert found.output["documents"][0]["doc_id"] == doc_id

    vec = h.handle(AgentRequest(intent=VECTORIZE_DOCUMENT, payload={"doc_id": doc_id}))
    assert vec.ok, vec.error
    assert vec.output["vectorization"]["points_upserted"] == store.count() > 0
    assert vec.output["vectorization"]["collection"] == "regs"
    assert vec.output["document"]["vectorized"] is True


def test_upload_requires_text_or_path(tmp_path):
    res = _handler(tmp_path).handle(AgentRequest(intent=UPLOAD_DOCUMENT, payload={}))
    assert not res.ok and "text" in res.error


def test_vectorize_unknown_document_fails(tmp_path):
    res = _handler(tmp_path).handle(AgentRequest(intent=VECTORIZE_DOCUMENT, payload={"doc_id": "nope"}))
    assert not res.ok and "nope" in res.error


def test_vectorize_unknown_profile_fails(tmp_path):
    h = _handler(tmp_path)
    up = h.handle(AgentRequest(intent=UPLOAD_DOCUMENT, payload={"text": MD}))
    res = h.handle(AgentRequest(intent=VECTORIZE_DOCUMENT,
                                payload={"doc_id": up.output["document"]["doc_id"], "profile": "ghost"}))
    assert not res.ok and "ghost" in res.error


def test_unsupported_intent(tmp_path):
    res = _handler(tmp_path).handle(AgentRequest(intent="dance", payload={}))
    assert not res.ok and "Unsupported intent" in res.error


def test_registry_wires_regulatory_docs_from_shipped_config():
    from agents.paths import AGENTS_CONFIG

    reg = AgentRegistry.from_config(AGENTS_CONFIG, handlers={AGENT_NAME: RegulatoryDocsHandler()})
    for intent in (UPLOAD_DOCUMENT, FIND_DOCUMENTS, VECTORIZE_DOCUMENT):
        assert reg.get_for_intent(intent).name == AGENT_NAME
