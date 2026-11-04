"""Dependency guard for the eflint_verifier tests (see conftest.py)."""
from __future__ import annotations

import importlib.util
import sys

import pytest

MISSING = [m for m in ("pydantic", "httpx") if importlib.util.find_spec(m) is None]
HINT = (f"eflint_verifier tests need {', '.join(MISSING)}, which is not installed for "
        f"{sys.executable}. Run them with the project environment: "
        "`uv run pytest tests/eflint_verifier` or `.venv/bin/python -m pytest tests/eflint_verifier`.")


def require_deps() -> None:
    """Call at the top of each test module, before importing the agent: skip with a reason."""
    if MISSING:
        pytest.skip(HINT, allow_module_level=True)
