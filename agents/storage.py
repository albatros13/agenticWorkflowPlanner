"""Storage location resolution (see ``config/storage.json``).

Agents ask for a *named* storage location instead of hard-coding paths, so the backend can be
swapped for a cloud database / object-store later by editing ``config/storage.json`` — not agent
code. Two kinds of location exist:

* **private** — one per agent, for that agent's own inputs and outputs
  (:func:`agent_storage`);
* **shared** — a single cross-agent exchange area (:func:`shared_storage`).

Only the ``local`` (filesystem) backend is implemented today; other kinds raise until wired.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .paths import CONFIG_DIR, REPO_ROOT

STORAGE_CONFIG = Path(os.getenv("STORAGE_CONFIG", str(CONFIG_DIR / "storage.json")))


@dataclass(frozen=True)
class StorageLocation:
    """A resolved storage backend location.

    For ``kind == "local"``, ``root`` is a filesystem directory. ``params`` carries any
    backend-specific settings (bucket, dsn, collection, …) for non-local kinds added later.
    """

    kind: str
    root: Path
    params: dict = field(default_factory=dict)

    def path(self, *parts: str) -> Path:
        """Return a filesystem path under this location (local backend only)."""
        if self.kind != "local":
            raise NotImplementedError(
                f"storage kind {self.kind!r} is not implemented yet — see config/storage.json"
            )
        return self.root.joinpath(*parts)


def _load() -> dict:
    return json.loads(STORAGE_CONFIG.read_text(encoding="utf-8"))


def _resolve(spec: dict) -> StorageLocation:
    kind = spec.get("kind", "local")
    root = Path(spec.get("path", ""))
    if not root.is_absolute():
        root = REPO_ROOT / root
    params = {k: v for k, v in spec.items() if k not in ("kind", "path")}
    return StorageLocation(kind=kind, root=root, params=params)


def agent_storage(agent: str) -> StorageLocation:
    """Private storage location for ``agent`` (its own inputs/outputs)."""
    agents = _load().get("agents", {})
    if agent not in agents:
        raise KeyError(f"No storage configured for agent {agent!r} in {STORAGE_CONFIG}")
    return _resolve(agents[agent])


def shared_storage() -> StorageLocation:
    """The cross-agent shared exchange location."""
    return _resolve(_load().get("shared", {"kind": "local", "path": "storage"}))
