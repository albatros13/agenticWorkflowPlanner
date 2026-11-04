"""Viewing stored runs: agents/text_to_bpmn/runs.py and the demo's /api/runs endpoints (offline)."""
import json

import pytest
from fastapi.testclient import TestClient

import agents.text_to_bpmn.demo.main as demo
from agents.contract import AgentRequest
from agents.text_to_bpmn.agent import ENGINEER_PROCESS, TextToBpmnHandler
from agents.text_to_bpmn.runs import RunNotFoundError, list_runs, load_run

from .._bpmn_fixtures import TEXT, scripted_provider


@pytest.fixture
def runs(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    monkeypatch.setenv("RUNS_DIR", str(root))
    return root


def generate() -> dict:
    result = TextToBpmnHandler(provider=scripted_provider()).handle(
        AgentRequest(intent=ENGINEER_PROCESS, payload={"text": TEXT, "process_name": "Follow-up"}))
    assert result.ok, result.error
    return result.output


def test_a_stored_run_reloads_exactly_as_it_was_generated(runs):
    fresh = generate()
    stored = load_run(fresh["run_id"])
    for key in ("run_id", "provider", "bpmn_xml", "dmn_xml", "resources", "validation"):
        assert stored[key] == fresh[key], key
    assert stored["stored"] is True and stored["warnings"] == []
    assert stored["process_name"] == "Follow-up" and stored["source_text"] == TEXT.strip()  # the loader strips it
    assert stored["saved_at"]


def test_list_runs_newest_first_and_only_runs_with_a_model(runs):
    first, second = generate()["run_id"], generate()["run_id"]
    (runs / "incomplete").mkdir()                       # pipeline stopped before compilation
    (runs / "incomplete" / "document.json").write_text("{}")
    rows = list_runs()
    assert [r["run_id"] for r in rows] == [second, first]
    assert rows[0]["process_name"] == "Follow-up" and rows[0]["provider"] == "mock"
    assert rows[0]["nodes"] and rows[0]["has_dmn"] is True


def test_unreadable_artifacts_still_show_the_diagram_with_warnings(runs):
    run_id = generate()["run_id"]
    ir = runs / run_id / "ir.json"
    rec = json.loads(ir.read_text())
    rec["data"] = {"name": "old schema"}                 # e.g. written by an older pipeline version
    ir.write_text(json.dumps(rec))
    (runs / run_id / "evidence.json").unlink()

    stored = load_run(run_id)
    assert stored["bpmn_xml"].lstrip().startswith("<?xml") and stored["validation"]["bpmn_valid"]
    assert any("ir.json could not be read" in w for w in stored["warnings"])
    assert "evidence.json is missing" in stored["warnings"]
    assert stored["resources"]["elements"] == [] and stored["resources"]["counts"]["nodes"] == 0


@pytest.mark.parametrize("run_id", ["missing", "../etc", ""])
def test_unknown_or_invalid_runs(runs, run_id):
    with pytest.raises(RunNotFoundError):
        load_run(run_id)


def test_demo_lists_and_returns_stored_runs(runs):
    run_id = generate()["run_id"]
    client = TestClient(demo.app)
    listing = client.get("/api/runs").json()
    assert [r["run_id"] for r in listing["runs"]] == [run_id] and listing["runs_dir"] == str(runs)
    body = client.get(f"/api/runs/{run_id}").json()
    assert body["stored"] is True and body["validation"]["bpmn_valid"] and body["resources"]["counts"]["nodes"]
    assert client.get("/api/runs/missing").status_code == 404
