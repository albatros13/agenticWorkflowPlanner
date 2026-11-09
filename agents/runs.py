"""Where each agent keeps its runs, and how a run id is validated — shared by every agent.

A run is one folder ``<runs root>/<run_id>/`` of JSON/text artifacts. The root is an explicit
override (constructor argument, used by tests), else the agent's env var (ops), else
``runs`` in the agent's private storage (``config/storage.json``).
"""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

from agents.storage import agent_storage

# Run folder names: what ``new_run_id`` produces, and nothing that can leave the runs root.
RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def runs_root(agent: str, env_var: str, override: Path | None = None) -> Path:
    if override is not None:
        return Path(override)
    env = os.getenv(env_var)
    return Path(env) if env else agent_storage(agent).path("runs")


def is_run_id(run_id: str | None) -> bool:
    return isinstance(run_id, str) and bool(RUN_ID.match(run_id))


def read_json(path: Path) -> dict | None:
    """The JSON object in ``path``, or None when the file does not exist."""
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
