"""text->BPMN agent handler: wraps the unchanged pipeline behind the agent contract."""
from agents.contract import AgentRequest
from agents.contract.models import JobStatus
from agents.text_to_bpmn.agent import ENGINEER_PROCESS, TextToBpmnHandler

from .._bpmn_fixtures import TEXT, scripted_provider


def test_handle_engineer_process(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DIR", str(tmp_path))
    handler = TextToBpmnHandler(provider=scripted_provider())
    result = handler.handle(
        AgentRequest(intent=ENGINEER_PROCESS, payload={"text": TEXT, "process_name": "Follow-up"})
    )
    assert result.ok
    assert result.agent == "text_to_bpmn"
    assert result.artifacts_ref  # run_id
    out = result.output
    assert out["bpmn_xml"].lstrip().startswith("<?xml")
    assert out["validation"]["bpmn_valid"] is True
    assert out["validation"]["dmn_valid"] is True
    assert "Family physician" in out["resources"]["actors"]


def test_handle_rejects_wrong_intent():
    result = TextToBpmnHandler(provider=scripted_provider()).handle(
        AgentRequest(intent="something_else", payload={"text": TEXT})
    )
    assert result.status == JobStatus.failed
    assert "Unsupported intent" in result.error


def test_handle_rejects_empty_text():
    result = TextToBpmnHandler(provider=scripted_provider()).handle(
        AgentRequest(intent=ENGINEER_PROCESS, payload={"text": "   "})
    )
    assert result.status == JobStatus.failed
    assert "text is required" in result.error


def test_manifest_advertises_capability():
    manifest = TextToBpmnHandler().manifest()
    assert manifest.name == "text_to_bpmn"
    assert manifest.serves(ENGINEER_PROCESS)
