"""Cross-agent hand-off of a project's current model, kept in the *shared* storage area.

Agents never read each other's private storage. When the text->BPMN agent produces a model, the
coordinator places a copy of the BPMN XML here (``<root>/<project_id>/model.bpmn``) so that a
downstream agent (e.g. the Petri net generator) can be handed "the project's model" later without
re-generating it from text.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from agents.storage import shared_storage


class ModelHandoff:
    def __init__(self, root: Path | None = None):
        if root is None:
            env = os.getenv("HANDOFF_DIR")
            root = Path(env) if env else shared_storage().path("projects")
        self.root = Path(root)

    def _path(self, project_id: str) -> Path:
        return self.root / project_id / "model.bpmn"

    def save_bpmn(self, project_id: str, bpmn_xml: str) -> None:
        path = self._path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(bpmn_xml, encoding="utf-8")

    def load_bpmn(self, project_id: str) -> str | None:
        path = self._path(project_id)
        return path.read_text(encoding="utf-8") if path.exists() else None

    def remove(self, project_id: str) -> None:
        shutil.rmtree(self.root / project_id, ignore_errors=True)
