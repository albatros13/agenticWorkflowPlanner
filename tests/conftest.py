"""Suite-wide guard: the tests must run in the project environment.

The agents' dependencies (pydantic, fastapi, ...) are installed in the project's ``.venv``, not in
a system Python. A system ``pytest`` (e.g. ``/usr/bin/pytest``) would otherwise import nothing
and report "collected 0 items" or skip whole folders without saying why. Stop with a clear
message instead.
"""
import importlib.util
import sys

import pytest

_CORE = ("pydantic", "fastapi")


def pytest_configure(config):
    missing = [m for m in _CORE if importlib.util.find_spec(m) is None]
    if missing:
        raise pytest.UsageError(
            f"pytest is running on {sys.executable}, which lacks the project's dependencies "
            f"({', '.join(missing)}). Run the tests in the project environment: `uv run pytest ...`, "
            "`.venv/bin/python -m pytest ...`, or `pytest ...` after `source .venv/bin/activate`.")
