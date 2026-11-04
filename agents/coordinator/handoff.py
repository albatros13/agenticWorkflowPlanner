"""Cross-agent hand-off of a project's current model, kept in the *shared* storage area.

Agents never read each other's private storage. When the text->BPMN agent produces a model, the
coordinator places a copy of the BPMN XML here (``<root>/<project_id>/model.bpmn``) so that a
downstream agent (e.g. the Petri net generator) can be handed "the project's model" later without
re-generating it from text.

The same area holds the other per-project artifacts downstream agents consume: the Petri net
(``model.pnml``, from the Petri net generator) and the eFLINT verification inputs supplied by the
user (``spec.eflint``, ``rules.qr``, ``checks.analysis``), so "verify again" needs no re-upload.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from agents.storage import shared_storage


BPMN = "model.bpmn"
PNML = "model.pnml"
PETRI_NET_TEXT = "model.pn"
EFLINT = "spec.eflint"
QUALIFICATION = "rules.qr"
ANALYSIS = "checks.analysis"


class ModelHandoff:
    def __init__(self, root: Path | None = None):
        if root is None:
            env = os.getenv("HANDOFF_DIR")
            root = Path(env) if env else shared_storage().path("projects")
        self.root = Path(root)

    def _path(self, project_id: str, filename: str = BPMN) -> Path:
        if Path(filename).name != filename or filename in ("", ".", ".."):
            raise ValueError(f"invalid artifact name {filename!r}")
        return self.root / project_id / filename

    def save(self, project_id: str, filename: str, content: str) -> None:
        path = self._path(project_id, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def load(self, project_id: str, filename: str) -> str | None:
        path = self._path(project_id, filename)
        return path.read_text(encoding="utf-8") if path.exists() else None

    def discard(self, project_id: str, filename: str) -> None:
        self._path(project_id, filename).unlink(missing_ok=True)

    def save_bpmn(self, project_id: str, bpmn_xml: str) -> None:
        self.save(project_id, BPMN, bpmn_xml)

    def load_bpmn(self, project_id: str) -> str | None:
        return self.load(project_id, BPMN)

    def remove(self, project_id: str) -> None:
        shutil.rmtree(self.root / project_id, ignore_errors=True)
