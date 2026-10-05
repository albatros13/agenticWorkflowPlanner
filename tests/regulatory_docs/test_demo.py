"""Demo API — offline via an injected hash embedder + in-memory store (no keys/network)."""
import agents.regulatory_docs.demo.main as demo
from fastapi.testclient import TestClient

from agents.regulatory_docs.agent import RegulatoryDocsHandler
from agents.regulatory_docs.config import VectorizationConfig
from agents.regulatory_docs.library import DocumentLibrary

from .conftest import InMemoryStore, offline_embedder_factory, store_factory_for

MD = "# Export Control\n\n## Clause 1\nDual-use goods require a licence.\n"

CFG = {
    "default_profile": "offline",
    "profiles": {"offline": {"embedding": {"provider": "hash", "dimension": 16},
                             "qdrant": {"cluster": "memory", "collection": "regs"}}},
}


def _client(tmp_path, store):
    handler = RegulatoryDocsHandler(
        library=DocumentLibrary(tmp_path),
        config=VectorizationConfig.model_validate(CFG),
        embedder_factory=offline_embedder_factory,
        store_factory=store_factory_for(store),
    )
    demo._handler = lambda: handler  # type: ignore[attr-defined]
    return TestClient(demo.app)


def test_upload_find_vectorize_over_http(tmp_path):
    store = InMemoryStore()
    client = _client(tmp_path, store)

    up = client.post("/api/upload", json={"text": MD, "title": "Export Control"})
    assert up.status_code == 200, up.text
    doc_id = up.json()["document"]["doc_id"]

    docs = client.get("/api/documents", params={"query": "export"})
    assert docs.status_code == 200 and docs.json()["count"] == 1

    profiles = client.get("/api/profiles")
    assert any(p["name"] == "offline" for p in profiles.json()["profiles"])

    vec = client.post("/api/vectorize", json={"doc_id": doc_id, "profile": "offline"})
    assert vec.status_code == 200, vec.text
    assert vec.json()["vectorization"]["points_upserted"] == store.count() > 0


def test_vectorize_unknown_doc_is_422(tmp_path):
    client = _client(tmp_path, InMemoryStore())
    resp = client.post("/api/vectorize", json={"doc_id": "missing"})
    assert resp.status_code == 422


def test_empty_upload_rejected(tmp_path):
    client = _client(tmp_path, InMemoryStore())
    assert client.post("/api/upload", json={"text": ""}).status_code == 422


def test_health_ok(tmp_path):
    _client(tmp_path, InMemoryStore())
    assert TestClient(demo.app).get("/api/health").json() == {"status": "ok"}
