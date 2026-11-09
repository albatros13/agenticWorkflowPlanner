"""Qdrant-backed semantic project cache — SKELETON, not yet implemented.

Provisioned so the wiring exists (constructor signature, ``SEMANTIC_CACHE`` flag). The embedding +
upsert + search are deferred, so :func:`~agents.coordinator.cache.build_cache` refuses to select it
until then. When implementing, mirror :mod:`agents.regulatory_docs.qdrant_store` (inject an
embedder callable; reuse its ``build_qdrant_client`` for the ``QDRANT_*`` credentials).
"""
from __future__ import annotations

from typing import Callable

from .base import SemanticProjectCache, SimilarProject
from ..projects.models import Project

Embedder = Callable[[list[str]], list[list[float]]]

_DEFERRED = "The Qdrant semantic project cache is not implemented yet."


class QdrantSemanticCache(SemanticProjectCache):
    def __init__(
        self,
        embedder: Embedder | None = None,
        *,
        collection: str = "projects",
        url: str | None = None,
    ):
        # Intentionally does not connect yet: this is a provisioned seam.
        self._embed = embedder
        self._collection = collection
        self._url = url

    def index_project(self, project: Project) -> None:  # pragma: no cover - deferred
        raise NotImplementedError(_DEFERRED)

    def find_similar(  # pragma: no cover - deferred
        self, query: str, *, user_id: str | None = None, k: int = 5
    ) -> list[SimilarProject]:
        raise NotImplementedError(_DEFERRED)
