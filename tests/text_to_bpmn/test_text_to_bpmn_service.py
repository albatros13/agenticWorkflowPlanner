"""text->BPMN HTTP service + HttpAgent adapter, exercised via FastAPI TestClient.

This proves the *external agent* path: the coordinator's HttpAgent talks to the agent over the
REST contract with no coordinator code change (COORDINATOR_PLAN sec 4, 9).
"""
import agents.text_to_bpmn.service as svc
from agents.contract import AgentRequest
from agents.contract.base import HttpAgent
from fastapi.testclient import TestClient

from .._bpmn_fixtures import TEXT, scripted_provider


def test_manifest_and_invoke_sync(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DIR", str(tmp_path))
    monkeypatch.setattr(svc, "_handler", __import__(
        "agents.text_to_bpmn.agent", fromlist=["TextToBpmnHandler"]
    ).TextToBpmnHandler(provider=scripted_provider()))
    client = TestClient(svc.app)

    manifest = client.get("/agent/manifest").json()
    assert manifest["name"] == "text_to_bpmn"

    resp = client.post(
        "/agent/invoke_sync",
        json={"intent": "engineer_process", "payload": {"text": TEXT, "process_name": "Follow-up"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["output"]["validation"]["bpmn_valid"] is True


def test_http_agent_against_testclient(tmp_path, monkeypatch):
    """HttpAgent speaks the REST contract; drive it against the in-process ASGI app.

    Starlette's TestClient is a synchronous httpx client that speaks ASGI, so we swap it in as
    HttpAgent's client factory — exercising the exact HTTP code paths without a live server.
    """
    monkeypatch.setenv("RUNS_DIR", str(tmp_path))
    monkeypatch.setattr(svc, "_handler", __import__(
        "agents.text_to_bpmn.agent", fromlist=["TextToBpmnHandler"]
    ).TextToBpmnHandler(provider=scripted_provider()))

    agent = HttpAgent("text_to_bpmn", "http://testserver")
    monkeypatch.setattr(agent, "_client", lambda: TestClient(svc.app, base_url="http://testserver"))

    assert agent.manifest().serves("engineer_process")
    result = agent.invoke(AgentRequest(intent="engineer_process", payload={"text": TEXT}))
    assert result.ok
    assert result.output["validation"]["bpmn_valid"] is True
