"""Semantic project cache protocol."""
from __future__ import annotations

import abc

from pydantic import BaseModel

from ..projects.models import Project, ProjectRef


class SimilarProject(BaseModel):
    ref: ProjectRef
    score: float


class SemanticProjectCache(abc.ABC):
    """Indexes project descriptions and finds semantically similar existing projects."""

    @abc.abstractmethod
    def index_project(self, project: Project) -> None:
        """Add or update a project's embedding in the cache."""

    @abc.abstractmethod
    def find_similar(
        self, query: str, *, user_id: str | None = None, k: int = 5, min_score: float = 0.0
    ) -> list[SimilarProject]:
        """Return up to ``k`` existing projects similar to ``query`` (by embedding distance)."""

    def remove(self, project_id: str) -> None:  # optional; default no-op
        return None
