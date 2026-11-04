"""The agent needs pydantic (shared contract) and httpx (its HTTP client). They live in the project
environment, not in a system Python, so a system ``pytest`` (e.g. ``/usr/bin/pytest``) finds
neither. Instead of silently collecting nothing, each test module skips with an explicit reason
(``_deps.require_deps``), and with ``EFLINT_VERIFIER_LIVE=1`` — an explicit request to run against
the real service — missing dependencies are a usage error.
"""
import os

import pytest

from ._deps import HINT, MISSING


def pytest_configure(config):
    if MISSING and os.getenv("EFLINT_VERIFIER_LIVE"):
        raise pytest.UsageError(HINT)
