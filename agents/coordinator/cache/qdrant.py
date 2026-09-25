"""Qdrant-backed semantic project cache — SKELETON, not yet implemented.

Provisioned so the wiring exists (constructor signature, config flag, reuse of the existing
Qdrant credentials in ``.env`` / ``api/qdrant_remote_client.py``). The embedding + upsert +
search are deferred; see COORDINATOR_PLAN sec 8. When implementing, mirror the pattern in
``src/retrieval/qdrant_index.py`` (inject an embedder callable; keep it out of this module).
"""
from __future__ import annotations

from typing import Callable

from .base import SemanticProjectCache, SimilarProject
from ..projects.models import Project

Embedder = Callable[[list[str]], list[list[float]]]

_DEFERRED = "Semantic project cache is deferred — see todo/COORDINATOR_PLAN.md sec 8."


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
        self, query: str, *, user_id: str | None = None, k: int = 5, min_score: float = 0.0
    ) -> list[SimilarProject]:
        raise NotImplementedError(_DEFERRED)
