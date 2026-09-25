"""Canonical filesystem locations for the agent layer.

Overridable via env vars so the same code runs embedded (dev) or as separate services (prod)
without edits — see COORDINATOR_PLAN sec 10.
"""
from __future__ import annotations

import os
from pathlib import Path

# paths.py lives at agents/paths.py -> repo root is 2 levels up. Kept independent of any single
# agent's package so shared coordinator infra does not import a specific agent's source.
REPO_ROOT = Path(__file__).resolve().parents[1]

CONFIG_DIR = REPO_ROOT / "config"
AGENTS_CONFIG = Path(os.getenv("AGENTS_CONFIG", str(CONFIG_DIR / "agents.json")))
PROJECT_STATES_CONFIG = Path(
    os.getenv("PROJECT_STATES_CONFIG", str(CONFIG_DIR / "project-states.json"))
)

# Per-agent private + shared storage locations live in config/storage.json; resolve them via
# agents.storage.agent_storage() / shared_storage() rather than hard-coding paths here.
