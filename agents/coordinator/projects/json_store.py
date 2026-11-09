"""Filesystem/JSON project store.

One file per project at ``<projects_dir>/<user_id>/<project_id>.json``, written atomically via a
temp-file rename. Mirrors the plain-JSON style of
:class:`agents.text_to_bpmn.pipeline.pipeline.artifacts.ArtifactStore`.

The default location is the coordinator's private storage (``config/storage.json``); the
``PROJECTS_DIR`` env var overrides it (used by tests/ops).

User and project ids are validated (:func:`agents.storage.safe_id`), never rewritten: rewriting
made distinct ids collide (``a/b`` and ``a_b``) and let ``..`` escape the store. An invalid user id
raises ``ValueError``; an invalid project id cannot exist, so it is reported as not found.
"""
from __future__ import annotations

import os
from pathlib import Path

from agents.storage import agent_storage, is_safe_id, safe_id

from .models import Project, ProjectRef
from .store import ProjectNotFoundError, ProjectStore


class JsonProjectStore(ProjectStore):
    def __init__(self, root: Path | None = None):
        if root is None:
            env = os.getenv("PROJECTS_DIR")
            root = Path(env) if env else agent_storage("coordinator").path("projects")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _user_dir(self, user_id: str) -> Path:
        return self.root / safe_id(user_id, "user id")

    def _path(self, user_id: str, project_id: str) -> Path:
        if not is_safe_id(project_id):
            raise ProjectNotFoundError(f"Project {project_id!r} not found for {user_id!r}")
        return self._user_dir(user_id) / f"{project_id}.json"

    def _write(self, project: Project) -> None:
        path = self._path(project.user_id, project.id)
        path.parent.mkdir(parents=True, exist_ok=True)  # created on first write, never on reads
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(project.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, path)  # atomic on POSIX

    def create(self, project: Project) -> Project:
        path = self._path(project.user_id, project.id)
        if path.exists():
            raise FileExistsError(f"Project {project.id!r} already exists for {project.user_id!r}")
        self._write(project)
        return project

    def get(self, user_id: str, project_id: str) -> Project:
        path = self._path(user_id, project_id)
        if not path.exists():
            raise ProjectNotFoundError(f"Project {project_id!r} not found for {user_id!r}")
        return Project.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, project: Project) -> Project:
        self._write(project)
        return project

    def _projects(self, user_id: str) -> list[Project]:
        d = self._user_dir(user_id)
        if not d.is_dir():
            return []
        return [Project.model_validate_json(p.read_text(encoding="utf-8")) for p in d.glob("*.json")]

    def list(self, user_id: str) -> list[ProjectRef]:
        refs = [p.ref() for p in self._projects(user_id)]
        return sorted(refs, key=lambda r: r.updated_at, reverse=True)

    def search(self, user_id: str, query: str) -> list[ProjectRef]:
        """Substring search over name/description, reading each project file once."""
        q = query.lower().strip()
        hits = [p.ref() for p in self._projects(user_id)
                if q in p.name.lower() or q in p.description.lower()]
        return sorted(hits, key=lambda r: r.updated_at, reverse=True)

    def delete(self, user_id: str, project_id: str) -> None:
        path = self._path(user_id, project_id)
        if not path.exists():
            raise ProjectNotFoundError(f"Project {project_id!r} not found for {user_id!r}")
        path.unlink()
