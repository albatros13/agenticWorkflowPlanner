"""Coordinator FastAPI service smoke test (offline, in-process agent)."""
import agents.coordinator.service as svc
from agents.contract import AgentRegistry, InProcessAgent
from agents.coordinator.coordinator import Coordinator
from agents.coordinator.intent import HeuristicIntentClassifier
from agents.coordinator.projects.json_store import JsonProjectStore
from agents.coordinator.state.machine import StateMachine
from agents.paths import PROJECT_STATES_CONFIG
from agents.text_to_bpmn.agent import TextToBpmnHandler
from fastapi.testclient import TestClient

from ._bpmn_fixtures import TEXT, scripted_provider


def _coordinator(tmp_path):
    handler = TextToBpmnHandler(provider=scripted_provider())
    registry = AgentRegistry(
        agents={"text_to_bpmn": InProcessAgent(handler)},
        intent_map={"engineer_process": "text_to_bpmn"},
    )
    return Coordinator(
        registry=registry,
        store=JsonProjectStore(tmp_path / "projects"),
        machine=StateMachine.from_config(PROJECT_STATES_CONFIG),
        classifier=HeuristicIntentClassifier(),
    )


def test_message_and_project_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(svc, "_coordinator", _coordinator(tmp_path))
    client = TestClient(svc.app)

    resp = client.post("/coordinator/message", json={"text": TEXT, "user_id": "u@x.com"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "engineer_process"
    assert body["state"] == "MODEL_IN_PROGRESS"
    pid = body["project"]["id"]

    listing = client.get("/coordinator/projects", params={"user_id": "u@x.com"}).json()
    assert any(p["id"] == pid for p in listing["projects"])

    detail = client.get(f"/coordinator/projects/{pid}", params={"user_id": "u@x.com"}).json()
    assert detail["state"] == "MODEL_IN_PROGRESS"
    assert detail["runs"]

    missing = client.get("/coordinator/projects/nope", params={"user_id": "u@x.com"})
    assert missing.status_code == 404

    # Delete endpoint (used by the UI) removes the project.
    deleted = client.delete(f"/coordinator/projects/{pid}", params={"user_id": "u@x.com"})
    assert deleted.status_code == 200 and deleted.json()["deleted"] == pid
    assert client.get("/coordinator/projects", params={"user_id": "u@x.com"}).json()["projects"] == []
    assert client.delete(f"/coordinator/projects/{pid}", params={"user_id": "u@x.com"}).status_code == 404


def test_index_page_served():
    from fastapi.testclient import TestClient
    import agents.coordinator.service as svc

    resp = TestClient(svc.app).get("/")
    assert resp.status_code == 200
    assert "Coordinator" in resp.text
