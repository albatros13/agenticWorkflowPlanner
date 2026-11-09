"""Qdrant vector store for vectorized regulatory documents.

Wraps a Qdrant client whose cluster is chosen by the profile:

* ``main`` / ``kg`` connect to the **cloud** Qdrant cluster, reading the endpoint and key
  straight from the environment — ``QDRANT_URL`` / ``QDRANT_API_KEY`` for ``main`` and
  ``QDRANT_URL_KG`` / ``QDRANT_API_KEY_KG`` for ``kg``. A local ``.env`` is seeded into the
  environment (via the repo's dependency-free loader) so no third-party ``dotenv`` is needed.
* ``memory`` uses qdrant-client's in-process local mode (dev/tests).

Both the client and the ``qdrant-client`` import are lazy, so the core package installs without
the ``retrieval`` extra. The store is injectable into the vectorizer, so tests can substitute an
in-memory fake with no dependency at all (see :class:`VectorStore`).
"""
from __future__ import annotations

import os
from typing import Any, Protocol

from agents.text_to_bpmn.pipeline.config import load_dotenv

from .config import QdrantConfig

# Per-cluster env var names for the cloud endpoint + key. Secrets live in ``.env``, never in config.
_CLOUD_CREDENTIALS = {
    "main": ("QDRANT_URL", "QDRANT_API_KEY"),
    "kg": ("QDRANT_URL_KG", "QDRANT_API_KEY_KG"),
}

# Map our config's distance names onto qdrant's Distance enum member names (which are UPPERCASE —
# e.g. Distance.COSINE whose *value* is "Cosine"; indexing by the value raises KeyError).
_DISTANCES = {
    "cosine": "COSINE",
    "dot": "DOT",
    "euclid": "EUCLID",
    "euclidean": "EUCLID",
    "manhattan": "MANHATTAN",
}


class VectorStore(Protocol):
    """Minimal surface the vectorizer needs — lets tests inject a fake with no Qdrant."""

    def ensure_collection(self, *, dimension: int, distance: str, recreate: bool) -> None: ...

    def upsert(self, points: list[dict[str, Any]]) -> int: ...

    def delete_document(self, doc_id: str) -> None: ...

    def count(self) -> int: ...

    def list_documents(self) -> list[dict[str, Any]]: ...

    def search(self, vector: list[float], limit: int) -> list[dict[str, Any]]: ...


# Payload fields needed to summarise a collection per document (no text/vectors transferred).
_DOC_FIELDS = ["doc_id", "title", "source_document"]
_SCROLL_PAGE = 256


def summarize_documents(payloads: Any) -> list[dict[str, Any]]:
    """Group chunk payloads by ``doc_id`` into ``{doc_id, title, source_document, chunks}``."""
    docs: dict[str, dict[str, Any]] = {}
    for raw in payloads:
        payload = raw or {}
        doc_id = payload.get("doc_id")
        if not doc_id:
            continue
        entry = docs.setdefault(
            doc_id,
            {
                "doc_id": doc_id,
                "title": payload.get("title") or payload.get("source_document") or doc_id,
                "source_document": payload.get("source_document") or "",
                "chunks": 0,
            },
        )
        entry["chunks"] += 1
    return sorted(docs.values(), key=lambda d: d["title"].lower())


class QdrantDocumentStore:
    """Concrete :class:`VectorStore` backed by a live Qdrant client."""

    def __init__(self, client: Any, collection: str):
        self._client = client
        self._collection = collection

    def ensure_collection(self, *, dimension: int, distance: str = "cosine", recreate: bool = False) -> None:
        from qdrant_client.models import Distance, PayloadSchemaType, VectorParams  # noqa: PLC0415 - optional dep

        dist = Distance[_DISTANCES.get(distance.lower(), "COSINE")]
        params = VectorParams(size=dimension, distance=dist)
        exists = self._client.collection_exists(self._collection)
        if recreate and exists:
            self._client.delete_collection(self._collection)
            exists = False
        if not exists:
            self._client.create_collection(self._collection, vectors_config=params)
        # Filtering by doc_id (delete_document) needs a keyword index on cloud clusters; creating
        # an existing index is a no-op, so this also upgrades collections made before it existed.
        self._client.create_payload_index(self._collection, field_name="doc_id",
                                          field_schema=PayloadSchemaType.KEYWORD)

    def upsert(self, points: list[dict[str, Any]]) -> int:
        from qdrant_client.models import PointStruct  # noqa: PLC0415 - optional dep

        structs = [
            PointStruct(id=p["id"], vector=p["vector"], payload=p["payload"]) for p in points
        ]
        self._client.upsert(self._collection, points=structs)
        return len(structs)

    def delete_document(self, doc_id: str) -> None:
        """Remove every chunk of ``doc_id`` (re-vectorizing must not leave stale chunks behind)."""
        from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue  # noqa: PLC0415

        if not self._client.collection_exists(self._collection):
            return
        self._client.delete(self._collection, points_selector=FilterSelector(filter=Filter(
            must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))])))

    def count(self) -> int:
        return int(self._client.count(self._collection, exact=True).count)

    def list_documents(self) -> list[dict[str, Any]]:
        """Distinct documents stored in the collection (empty if it does not exist yet)."""
        if not self._client.collection_exists(self._collection):
            return []

        def payloads():
            offset = None
            while True:
                points, offset = self._client.scroll(
                    self._collection,
                    limit=_SCROLL_PAGE,
                    offset=offset,
                    with_payload=_DOC_FIELDS,
                    with_vectors=False,
                )
                for point in points:
                    yield point.payload
                if offset is None:
                    break

        return summarize_documents(payloads())

    def search(self, vector: list[float], limit: int = 5) -> list[dict[str, Any]]:
        """Nearest chunks to ``vector``: ``[{score, **payload}]`` (empty if no collection)."""
        if not self._client.collection_exists(self._collection):
            return []
        hits = self._client.query_points(
            self._collection, query=vector, limit=limit, with_payload=True
        ).points
        return [{"score": float(h.score), **(h.payload or {})} for h in hits]


def build_qdrant_client(cfg: QdrantConfig) -> Any:
    """Resolve a Qdrant client for the profile's cluster (lazy import).

    ``main``/``kg`` open a connection to the cloud cluster from ``.env`` credentials; ``memory``
    is qdrant-client's in-process mode for dev/tests.
    """
    try:
        from qdrant_client import QdrantClient  # noqa: PLC0415 - optional dep
    except ImportError as exc:  # pragma: no cover - optional dep
        raise RuntimeError(
            "Vectorizing to Qdrant requires 'qdrant-client' (`pip install .[retrieval]`)."
        ) from exc

    cluster = (cfg.cluster or "main").lower()
    if cluster == "memory":
        return QdrantClient(":memory:")
    if cluster in _CLOUD_CREDENTIALS:
        url_key, api_key_key = _CLOUD_CREDENTIALS[cluster]
        load_dotenv()  # seed QDRANT_* from a local .env without a third-party dependency
        url = os.getenv(url_key)
        api_key = os.getenv(api_key_key)
        if not (url and api_key):
            raise RuntimeError(
                f"Cloud Qdrant cluster {cluster!r} needs {url_key} and {api_key_key} in the "
                f"environment or .env."
            )
        return QdrantClient(url=url, api_key=api_key, https=True)
    raise ValueError(
        f"Unknown qdrant cluster {cfg.cluster!r} (expected 'main', 'kg', or 'memory')."
    )


def build_store(cfg: QdrantConfig) -> QdrantDocumentStore:
    return QdrantDocumentStore(build_qdrant_client(cfg), cfg.collection)
