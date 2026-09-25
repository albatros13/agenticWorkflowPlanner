"""Project store protocol.

Kept abstract so the JSON-on-disk implementation can be swapped for a database (Postgres/Neo4j
credentials already exist in ``.env``) without touching coordinator logic.
"""
from __future__ import annotations

import abc

from .models import Project, ProjectRef


class ProjectNotFoundError(KeyError):
    """Raised when a project id does not exist for the given user."""


class ProjectStore(abc.ABC):
    @abc.abstractmethod
    def create(self, project: Project) -> Project: ...

    @abc.abstractmethod
    def get(self, user_id: str, project_id: str) -> Project: ...

    @abc.abstractmethod
    def save(self, project: Project) -> Project: ...

    @abc.abstractmethod
    def list(self, user_id: str) -> list[ProjectRef]: ...

    @abc.abstractmethod
    def delete(self, user_id: str, project_id: str) -> None: ...

    def search(self, user_id: str, query: str) -> list[ProjectRef]:
        """Naive fallback search (substring over name/description).

        Semantic search is provided separately by the semantic cache; this exists so listing
        and simple lookups work before the cache is implemented.
        """
        q = query.lower().strip()
        out: list[ProjectRef] = []
        for ref in self.list(user_id):
            project = self.get(user_id, ref.id)
            if q in project.name.lower() or q in project.description.lower():
                out.append(ref)
        return out
