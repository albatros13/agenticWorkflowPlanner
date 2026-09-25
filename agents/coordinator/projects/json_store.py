"""Filesystem/JSON project store.

One file per project at ``<projects_dir>/<user_id>/<project_id>.json``, written atomically via a
temp-file rename. Mirrors the plain-JSON style of
:class:`agents.text_to_bpmn.pipeline.pipeline.artifacts.ArtifactStore`.

The default location is the coordinator's private storage (``config/storage.json``); the
``PROJECTS_DIR`` env var overrides it (used by tests/ops).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from agents.storage import agent_storage

from .models import Project, ProjectRef
from .store import ProjectNotFoundError, ProjectStore


def _safe(component: str) -> str:
    """Keep ids/user-ids filesystem-safe (emails, uuids)."""
    return "".join(c if c.isalnum() or c in "-_.@" else "_" for c in component)


class JsonProjectStore(ProjectStore):
    def __init__(self, root: Path | None = None):
        if root is None:
            env = os.getenv("PROJECTS_DIR")
            root = Path(env) if env else agent_storage("coordinator").path("projects")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _user_dir(self, user_id: str) -> Path:
        d = self.root / _safe(user_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _path(self, user_id: str, project_id: str) -> Path:
        return self._user_dir(user_id) / f"{_safe(project_id)}.json"

    def _write(self, project: Project) -> None:
        path = self._path(project.user_id, project.id)
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

    def list(self, user_id: str) -> list[ProjectRef]:
        d = self._user_dir(user_id)
        refs = [
            Project.model_validate_json(p.read_text(encoding="utf-8")).ref()
            for p in d.glob("*.json")
            if not p.name.endswith(".json.tmp")
        ]
        return sorted(refs, key=lambda r: r.updated_at, reverse=True)

    def delete(self, user_id: str, project_id: str) -> None:
        path = self._path(user_id, project_id)
        if not path.exists():
            raise ProjectNotFoundError(f"Project {project_id!r} not found for {user_id!r}")
        path.unlink()
