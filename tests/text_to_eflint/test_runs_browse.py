"""Browsing stored eFLINT runs (agent helpers + demo endpoints) and truncated-output detection."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import agents.text_to_eflint.demo.main as demo
from agents.contract import AgentRequest
from agents.text_to_bpmn.pipeline.llm.mock import ScriptedLLMProvider
from agents.text_to_eflint.agent import GENERATE_EFLINT, TextToEflintHandler
from agents.text_to_eflint.generation.prompts import TASK_DRAFT

from ._eflint_samples import TEXT, spec


@pytest.fixture(autouse=True)
def _runs_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("EFLINT_RUNS_DIR", str(tmp_path / "runs"))


def _generate(**payload):
    h = TextToEflintHandler(provider=ScriptedLLMProvider({TASK_DRAFT: spec()}))
    res = h.handle(AgentRequest(intent=GENERATE_EFLINT, payload={"text": TEXT, **payload}))
    assert res.ok, res.error
    return res.output


def test_generate_saves_input_and_runs_are_listed_and_reopened():
    out = _generate(title="Consent", language="nl", domain_context="smart meters")
    h = TextToEflintHandler()

    (listed,) = h.list_runs()
    assert listed["run_id"] == out["run_id"] and listed["title"] == "GDPR consent"
    assert listed["language"] == "nl" and listed["has_input"] and listed["status"] == "succeeded"

    run = h.load_run(out["run_id"])
    assert run["files"] == out["files"] and run["spec"] == out["spec"]
    assert run["summary"] == out["summary"] and run["findings"] == out["findings"]
    assert run["input"]["text"] == TEXT and run["input"]["domain_context"] == "smart meters"
    names = list(run["files"])
    assert names[:3] == ["Ontology.eflint", "Norms.eflint", "Init.eflint"] and names[-1] == "spec.eflint"


def test_legacy_run_without_input_and_bad_ids(tmp_path):
    out = _generate()
    (tmp_path / "runs" / out["run_id"] / "input.json").unlink()  # runs saved before input.json existed
    (tmp_path / "runs" / "junk").mkdir()  # not a run: no report.json
    h = TextToEflintHandler()
    (listed,) = h.list_runs()
    assert listed["has_input"] is False and listed["created_at"]
    assert h.load_run(out["run_id"])["input"] == {}
    for bad in ("../etc", "junk", "", "a/b"):
        with pytest.raises(KeyError):
            h.load_run(bad)


def test_demo_run_endpoints():
    out = _generate()
    client = TestClient(demo.app)
    listed = client.get("/api/runs").json()
    assert listed["count"] == 1 and listed["runs"][0]["run_id"] == out["run_id"]
    run = client.get(f"/api/runs/{out['run_id']}")
    assert run.status_code == 200 and run.json()["files"] == out["files"]
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/runs/..%2F..%2Fconfig").status_code == 404


def test_index_file_viewer_has_unique_id():
    # Regression: the viewer <pre> used id="file", same as the upload input, so tabs never updated it.
    html = (demo.STATIC_DIR / "index.html").read_text()
    assert html.count('id="file"') == 1 and 'id="eflintFile"' in html


def test_anthropic_truncated_tool_output_is_reported(monkeypatch):
    client = pytest.importorskip("api.anthropic_client")
    response = SimpleNamespace(stop_reason="max_tokens",
                               content=[SimpleNamespace(type="tool_use", input={"facts": [{"source": "x"}]})])
    monkeypatch.setattr(client, "ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(client, "llm_client", SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: response)))
    with pytest.raises(RuntimeError, match="truncated at max_tokens=100"):
        client.ask_anthropic_structured(task="t", system="s", prompt="p", schema={}, max_tokens=100)
