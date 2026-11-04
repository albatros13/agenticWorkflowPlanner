"""Shared setup for the Petri net generator tests.

The worker needs PM4Py (the `petri_net` extra). Each test module calls
``pytest.importorskip("pm4py", ...)`` so a missing PM4Py shows up as a *reported* skip with its
reason, rather than silently collecting nothing.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _runs_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PETRI_NET_RUNS_DIR", str(tmp_path / "runs"))
