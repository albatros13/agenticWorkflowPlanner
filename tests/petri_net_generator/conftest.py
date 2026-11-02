"""Shared setup for the Petri net generator tests. Skips the package when PM4Py is absent."""
from __future__ import annotations

import importlib.util

import pytest

if importlib.util.find_spec("pm4py") is None:  # needs the `petri_net` extra
    collect_ignore_glob = ["test_*.py"]


@pytest.fixture(autouse=True)
def _runs_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PETRI_NET_RUNS_DIR", str(tmp_path / "runs"))
