"""Listing vectorized documents and semantic search — offline (hash embedder, fakes / in-process Qdrant)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import agents.regulatory_docs.demo.main as demo
from agents.contract import AgentRequest
from agents.regulatory_docs.agent import (
    LIST_VECTORIZED,
    SEARCH_VECTORS,
    UPLOAD_DOCUMENT,
    VECTORIZE_DOCUMENT,
    RegulatoryDocsHandler,
)
from agents.regulatory_docs.config import EmbeddingConfig, QdrantConfig, VectorizationConfig
from agents.regulatory_docs.embeddings import build_embedder
from agents.regulatory_docs.library import DocumentLibrary
from agents.regulatory_docs.qdrant_store import QdrantDocumentStore, summarize_documents

from .conftest import InMemoryStore, offline_embedder_factory, store_factory_for

MD = (
    "# Residence permits\n\n## Application\nAn application shall be signed by the applicant.\n\n"
    "## Notification\nAn order shall not take effect until it has been notified.\n"
)
CFG = {
    "default_profile": "offline",
    "profiles": {
        "offline": {"embedding": {"provider": "hash", "dimension": 32},
                    "chunking": {"max_chars": 80},
                    "qdrant": {"cluster": "memory", "collection": "regs"}},
        "alias": {"embedding": {"provider": "hash", "dimension": 32},
                  "qdrant": {"cluster": "memory", "collection": "regs"}},
    },
}


def _handler(library_root, store):
    return RegulatoryDocsHandler(
        library=DocumentLibrary(library_root),
        config=VectorizationConfig.model_validate(CFG),
        embedder_factory=offline_embedder_factory,
        store_factory=store_factory_for(store),
    )


def _vectorize(handler, title="Residence permits"):
    up = handler.handle(AgentRequest(intent=UPLOAD_DOCUMENT, payload={"text": MD, "title": title}))
    doc_id = up.output["document"]["doc_id"]
    vec = handler.handle(AgentRequest(intent=VECTORIZE_DOCUMENT, payload={"doc_id": doc_id}))
    assert vec.ok, vec.error
    return doc_id, vec.output["vectorization"]["points_upserted"]


def test_summarize_groups_by_doc_and_skips_unlabelled():
    docs = summarize_documents([
        {"doc_id": "b", "title": "Beta"}, {"doc_id": "a", "source_document": "a.md"},
        {"doc_id": "b", "title": "Beta"}, {}, None,
    ])
    assert docs == [
        {"doc_id": "a", "title": "a.md", "source_document": "a.md", "chunks": 1},
        {"doc_id": "b", "title": "Beta", "source_document": "", "chunks": 2},
    ]


def test_list_vectorized_includes_documents_missing_from_library(tmp_path):
    store = InMemoryStore()
    doc_id, points = _vectorize(_handler(tmp_path / "lib_a", store))

    # A different (empty) library pointing at the same collection still sees the document.
    out = _handler(tmp_path / "lib_b", store).handle(AgentRequest(intent=LIST_VECTORIZED, payload={}))
    assert out.ok, out.error
    (coll,) = out.output["collections"]  # both profiles share memory/regs -> one target
    assert coll["profiles"] == ["alias", "offline"] and coll["error"] is None
    (doc,) = coll["documents"]
    assert doc == {"doc_id": doc_id, "title": "Residence permits", "source_document": doc["source_document"],
                   "chunks": points, "in_library": False}


def test_list_vectorized_reports_per_collection_errors(tmp_path):
    def broken(cfg):
        raise ConnectionError("cluster down")

    h = RegulatoryDocsHandler(library=DocumentLibrary(tmp_path),
                              config=VectorizationConfig.model_validate(CFG),
                              embedder_factory=offline_embedder_factory, store_factory=broken)
    out = h.handle(AgentRequest(intent=LIST_VECTORIZED, payload={"profile": "offline"}))
    assert out.ok and out.output["collections"][0]["error"] == "ConnectionError: cluster down"


def test_search_returns_ranked_hits_with_provenance(tmp_path):
    store = InMemoryStore()
    h = _handler(tmp_path, store)
    doc_id, _ = _vectorize(h)
    out = h.handle(AgentRequest(intent=SEARCH_VECTORS,
                                payload={"query": "order shall not take effect until notified", "limit": 2}))
    assert out.ok, out.error
    hits = out.output["hits"]
    assert 1 <= len(hits) <= 2 and hits[0]["doc_id"] == doc_id and hits[0]["in_library"] is True
    assert "notified" in hits[0]["text"]
    assert hits == sorted(hits, key=lambda x: x["score"], reverse=True)


def test_search_requires_query(tmp_path):
    out = _handler(tmp_path, InMemoryStore()).handle(AgentRequest(intent=SEARCH_VECTORS, payload={"query": " "}))
    assert not out.ok


def test_demo_endpoints(tmp_path):
    store = InMemoryStore()
    handler = _handler(tmp_path, store)
    demo._handler = lambda: handler  # type: ignore[attr-defined]
    client = TestClient(demo.app)
    doc_id, _ = _vectorize(handler)

    listed = client.get("/api/vectorized").json()
    assert listed["count"] == 1 and listed["collections"][0]["documents"][0]["doc_id"] == doc_id

    res = client.post("/api/search", json={"query": "signed application", "limit": 3})
    assert res.status_code == 200, res.text
    assert res.json()["hits"][0]["doc_id"] == doc_id
    assert client.post("/api/search", json={"query": ""}).status_code == 422


def test_qdrant_store_list_and_search_in_process():
    qdrant_client = pytest.importorskip("qdrant_client")
    store = QdrantDocumentStore(qdrant_client.QdrantClient(":memory:"), "regs")
    assert store.list_documents() == [] and store.search([1.0, 0.0], 3) == []  # no collection yet

    embed = build_embedder(EmbeddingConfig(provider="hash", dimension=16))
    texts = ["licence for dual-use goods", "residence permit application", "residence permit fee"]
    store.ensure_collection(dimension=16, distance="cosine", recreate=False)
    store.upsert([
        {"id": i + 1, "vector": v, "payload": {"doc_id": "d1" if i else "d0", "title": f"T{i > 0}", "text": t}}
        for i, (t, v) in enumerate(zip(texts, embed(texts)))
    ])
    assert [(d["doc_id"], d["chunks"]) for d in store.list_documents()] == [("d0", 1), ("d1", 2)]
    hits = store.search(embed(["residence permit application"])[0], 2)
    assert hits[0]["text"] == "residence permit application" and hits[0]["score"] == pytest.approx(1.0, abs=1e-5)


def test_file_upload_keeps_original_filename(tmp_path):
    store = InMemoryStore()
    handler = _handler(tmp_path, store)
    demo._handler = lambda: handler  # type: ignore[attr-defined]
    resp = TestClient(demo.app).post(
        "/api/upload_file", files={"file": ("norms_residence.md", MD.encode(), "text/markdown")}
    )
    assert resp.status_code == 200, resp.text
    doc = resp.json()["document"]
    assert doc["title"] == doc["source_document"] == "norms_residence.md"
