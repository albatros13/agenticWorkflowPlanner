"""No-op semantic cache: the wired-in default.

Indexing is a no-op and ``find_similar`` always returns ``[]`` ("no similar projects"), so the
full coordinator flow runs today. Swap in :class:`~agents.coordinator.cache.qdrant.QdrantSemanticCache`
via the ``SEMANTIC_CACHE`` setting once the embedding/search is implemented.
"""
from __future__ import annotations

from .base import SemanticProjectCache, SimilarProject
from ..projects.models import Project


class NoOpSemanticCache(SemanticProjectCache):
    def index_project(self, project: Project) -> None:
        return None

    def find_similar(
        self, query: str, *, user_id: str | None = None, k: int = 5, min_score: float = 0.0
    ) -> list[SimilarProject]:
        return []
