"""Versioned artifact store for pipeline stages.

Each pipeline run gets its own directory under ``runs/``. Every stage writes its
output here so failures can be inspected, and downstream stages call
:meth:`ArtifactStore.require` — which raises rather than silently continuing with
incomplete data (guideline Step 1 validation rule). Artifacts are *never*
overwritten; re-saving a name creates a new version and moves ``<name>.json`` to
point at the latest (Step 17).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class MissingArtifactError(RuntimeError):
    """Raised when a required upstream artifact is absent."""


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj


class ArtifactStore:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def new_run(cls, runs_dir: Path, run_id: str) -> "ArtifactStore":
        return cls(Path(runs_dir) / run_id)

    def save(self, name: str, obj: Any, *, meta: dict | None = None) -> Path:
        """Persist ``obj`` under ``name`` as a new immutable version.

        Returns the path of the version file. ``<name>.json`` always points at
        the newest version.
        """
        payload = {
            "name": name,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "meta": meta or {},
            "data": _to_jsonable(obj),
        }
        version = 1 + len(list(self.run_dir.glob(f"{name}.v*.json")))
        versioned = self.run_dir / f"{name}.v{version}.json"
        versioned.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        # Latest pointer (a copy, so the store works on filesystems w/o symlinks).
        (self.run_dir / f"{name}.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        return versioned

    def has(self, name: str) -> bool:
        return (self.run_dir / f"{name}.json").exists()

    def load(self, name: str) -> Any:
        path = self.run_dir / f"{name}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))["data"]

    def require(self, name: str) -> Any:
        """Return the artifact's data or raise :class:`MissingArtifactError`."""
        if not self.has(name):
            raise MissingArtifactError(
                f"Required artifact {name!r} is missing from run {self.run_dir.name!r}. "
                "An upstream pipeline stage did not complete."
            )
        return self.load(name)

    @staticmethod
    def content_hash(text: str) -> str:
        """Stable hash of an input document, for reproducibility (Step 17)."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
