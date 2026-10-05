"""Integration: vectorizing really lands points in the CLOUD Qdrant cluster.

Unlike the offline suites (which inject an in-memory store), this exercises the agent's default
wiring — ``build_store`` opening a live connection to the ``main`` cluster using ``QDRANT_URL`` /
``QDRANT_API_KEY`` from ``.env`` — and then re-reads the cluster independently to prove the data
is actually there. A deterministic hash embedder is used so no OpenAI key/network is needed; only
Qdrant credentials are required. The test is skipped when ``qdrant-client`` or the credentials are
absent, and it writes to a throwaway, uniquely-named collection that it deletes afterwards so it
never touches real data.
"""
from __future__ import annotations

import os
import uuid

import pytest

from agents.text_to_bpmn.pipeline.config import load_dotenv

# Qdrant client is an optional dep; skip the whole module without it.
pytest.importorskip("qdrant_client")

load_dotenv()  # seed QDRANT_URL / QDRANT_API_KEY from .env for this run

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")

pytestmark = pytest.mark.skipif(
    not (QDRANT_URL and QDRANT_API_KEY),
    reason="QDRANT_URL/QDRANT_API_KEY not set; skipping cloud Qdrant integration test.",
)

from agents.contract import AgentRequest  # noqa: E402 - after importorskip/skipif guards
from agents.regulatory_docs.agent import (  # noqa: E402
    UPLOAD_DOCUMENT,
    VECTORIZE_DOCUMENT,
    RegulatoryDocsHandler,
)
from agents.regulatory_docs.config import QdrantConfig, VectorizationConfig  # noqa: E402
from agents.regulatory_docs.library import DocumentLibrary  # noqa: E402
from agents.regulatory_docs.qdrant_store import build_qdrant_client  # noqa: E402

MD = (
    "# Data Protection Regulation\n\n"
    "## Scope\nThis regulation applies to all data controllers and processors.\n\n"
    "## Obligations\nControllers must document their processing activities.\n"
)


@pytest.fixture
def cloud_collection():
    """A unique collection on the live ``main`` cluster, deleted after the test."""
    client = build_qdrant_client(QdrantConfig(cluster="main"))
    name = f"regdocs_it_{uuid.uuid4().hex[:8]}"
    yield client, name
    try:
        client.delete_collection(name)
    except Exception:  # pragma: no cover - best-effort cleanup
        pass


def _handler(tmp_path, collection, *, recreate=True):
    # Real store (default store_factory -> build_store -> cloud) + hash embedder (default
    # embedder_factory -> build_embedder, no OpenAI key needed because provider is 'hash').
    cfg = {
        "default_profile": "cloud-it",
        "profiles": {
            "cloud-it": {
                "embedding": {"provider": "hash", "dimension": 64},
                "qdrant": {
                    "cluster": "main",
                    "collection": collection,
                    "distance": "cosine",
                    "recreate_collection": recreate,
                },
            }
        },
    }
    return RegulatoryDocsHandler(
        library=DocumentLibrary(tmp_path),
        config=VectorizationConfig.model_validate(cfg),
    )


def test_vectorize_writes_to_cloud_qdrant(tmp_path, cloud_collection):
    client, collection = cloud_collection
    handler = _handler(tmp_path, collection)

    up = handler.handle(AgentRequest(intent=UPLOAD_DOCUMENT, payload={"text": MD, "title": "DPR"}))
    assert up.ok, up.error
    doc_id = up.output["document"]["doc_id"]

    vec = handler.handle(AgentRequest(intent=VECTORIZE_DOCUMENT, payload={"doc_id": doc_id}))
    assert vec.ok, vec.error
    upserted = vec.output["vectorization"]["points_upserted"]
    assert upserted > 0
    assert vec.output["vectorization"]["cluster"] == "main"
    assert vec.output["vectorization"]["collection"] == collection

    # Re-read the LIVE cluster independently: the points, their count, and provenance really landed.
    assert client.collection_exists(collection)
    assert client.count(collection, exact=True).count == upserted

    points, _ = client.scroll(collection, limit=upserted, with_payload=True)
    assert len(points) == upserted
    payload = points[0].payload
    assert payload["doc_id"] == doc_id
    assert payload["section"] and payload["text"]


def test_revectorize_is_idempotent_in_cloud(tmp_path, cloud_collection):
    client, collection = cloud_collection
    # recreate=False so the second pass relies on stable point ids to overwrite, not a wipe.
    handler = _handler(tmp_path, collection, recreate=False)

    up = handler.handle(AgentRequest(intent=UPLOAD_DOCUMENT, payload={"text": MD, "title": "DPR"}))
    doc_id = up.output["document"]["doc_id"]

    first = handler.handle(AgentRequest(intent=VECTORIZE_DOCUMENT, payload={"doc_id": doc_id}))
    assert first.ok, first.error
    count_after_first = client.count(collection, exact=True).count

    # Same (doc, chunk) ids -> stable point ids -> re-vectorizing overwrites, never duplicates.
    second = handler.handle(AgentRequest(intent=VECTORIZE_DOCUMENT, payload={"doc_id": doc_id}))
    assert second.ok, second.error
    assert client.count(collection, exact=True).count == count_after_first
