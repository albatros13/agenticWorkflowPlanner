"""Qdrant-backed retriever (optional).

Uses the credentials already present in ``.env`` (``QDRANT_URL`` /
``QDRANT_API_KEY``). Imported lazily so the core package installs without
``qdrant-client``. Embeddings are injected via a callable so the choice of
embedding model stays configurable and out of this module.
"""
from __future__ import annotations

import os
from typing import Callable

from ..ingestion.chunker import Chunk
from .base import EvidenceIndex, RetrievedChunk

Embedder = Callable[[list[str]], list[list[float]]]


class QdrantEvidenceIndex(EvidenceIndex):
    def __init__(self, embedder: Embedder, *, collection: str = "evidence", url: str | None = None):
        try:
            from qdrant_client import QdrantClient  # noqa: PLC0415 - optional dep
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError(
                "QdrantEvidenceIndex requires 'qdrant-client' (`pip install .[retrieval]`)."
            ) from exc
        self._client = QdrantClient(
            url=url or os.getenv("QDRANT_URL"), api_key=os.getenv("QDRANT_API_KEY")
        )
        self._embed = embedder
        self._collection = collection
        self._chunks: dict[str, Chunk] = {}

    def index(self, chunks: list[Chunk]) -> None:  # pragma: no cover - needs service
        from qdrant_client.models import Distance, PointStruct, VectorParams

        vectors = self._embed([c.text for c in chunks])
        dim = len(vectors[0]) if vectors else 0
        self._client.recreate_collection(
            self._collection, vectors_config=VectorParams(size=dim, distance=Distance.COSINE)
        )
        points = [
            PointStruct(id=i, vector=vec, payload={"chunk_id": c.id})
            for i, (c, vec) in enumerate(zip(chunks, vectors))
        ]
        self._client.upsert(self._collection, points=points)
        self._chunks = {c.id: c for c in chunks}

    def all_chunks(self) -> list[Chunk]:
        return list(self._chunks.values())

    def search(self, query: str, k: int = 5) -> list[RetrievedChunk]:  # pragma: no cover - needs service
        vec = self._embed([query])[0]
        hits = self._client.search(self._collection, query_vector=vec, limit=k)
        results: list[RetrievedChunk] = []
        for hit in hits:
            chunk = self._chunks.get(hit.payload.get("chunk_id"))
            if chunk is not None:
                results.append(RetrievedChunk(chunk=chunk, score=float(hit.score)))
        return results
