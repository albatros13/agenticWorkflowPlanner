"""The agent contract (text / IR / BPMN paths, clarification, failures) and the HTTP service."""
from __future__ import annotations

from fastapi.testclient import TestClient

from agents.contract import AgentRegistry, AgentRequest
from agents.paths import AGENTS_CONFIG
from agents.petri_net_generator.agent import GENERATE_PETRI_NET, PetriNetGeneratorHandler
from agents.petri_net_generator.text_to_ir import TASK
from agents.text_to_bpmn.pipeline.llm.mock import ScriptedLLMProvider

from ._samples import MINIMAL, XOR, act, ir

REQUEST_TEXT = ("A request is submitted and validated. If valid, approve it and execute it. "
                "If invalid, return it for revision and validate it again.")

REQUEST_IR = ir({"kind": "sequence", "steps": [
    act("submit", "Submit request"),
    {"kind": "loop", "body": act("validate", "Validate request"),
     "redo": act("revise", "Return for revision"),
     "exit_condition": "request is valid", "continue_condition": "request is invalid"},
    act("approve", "Approve request"),
    act("execute", "Execute request"),
]}, process_name="Request handling", assumptions=["Validation repeats until the request is valid."])


def invoke(handler: PetriNetGeneratorHandler, payload: dict):
    return handler.handle(AgentRequest(intent=GENERATE_PETRI_NET, payload=payload, correlation_id="test"))


def test_text_path_uses_llm_ir_and_returns_pnml():
    provider = ScriptedLLMProvider({TASK: REQUEST_IR})
    result = invoke(PetriNetGeneratorHandler(provider=provider), {"input_type": "text", "text": REQUEST_TEXT})
    assert result.ok, result.error
    out = result.output
    assert out["status"] == "succeeded" and out["validation"]["soundness"] == "passed"
    assert out["net_summary"]["visible_labels"] == sorted(
        ["Submit request", "Validate request", "Return for revision", "Approve request", "Execute request"])
    assert out["assumptions"] == ["Validation repeats until the request is valid."]
    assert out["ir"]["root"]["steps"][1]["kind"] == "loop"
    assert out["pnml"].startswith("<?xml")
    assert REQUEST_TEXT in provider.calls[0].prompt


def test_blocking_ambiguity_asks_for_clarification_instead_of_inventing():
    ambiguous = ir({"kind": "sequence", "steps": [act("a"), act("b")]}, ambiguities=[{
        "severity": "blocking", "message": "It is unclear whether checks run in parallel or are alternatives.",
        "question": "Are both checks always performed, or only one of them?", "element_ids": ["a", "b"]}])
    result = invoke(PetriNetGeneratorHandler(provider=ScriptedLLMProvider({TASK: ambiguous})),
                    {"input_type": "text", "text": "Do check a or b."})
    assert result.ok
    assert result.output["status"] == "needs_clarification"
    assert result.output["ambiguities"][0]["question"].startswith("Are both checks")
    assert result.output["pnml"] is None


def test_ir_path_and_invalid_ir():
    handler = PetriNetGeneratorHandler()
    assert invoke(handler, {"input_type": "ir", "ir": REQUEST_IR}).output["status"] == "succeeded"

    bad = invoke(handler, {"input_type": "ir", "ir": ir({"kind": "xor", "branches": [{"body": act("a")}]})})
    assert not bad.ok and bad.output["errors"][0]["code"] == "invalid_ir"


def test_bpmn_errors_and_bad_payloads_fail_cleanly():
    handler = PetriNetGeneratorHandler()
    malformed = invoke(handler, {"input_type": "bpmn_xml", "bpmn_xml": "<definitions"})
    assert not malformed.ok and "malformed_xml" in malformed.error and "Traceback" not in malformed.error

    missing = invoke(handler, {"input_type": "bpmn_xml", "text": "wrong field"})
    assert not missing.ok and "requires a non-empty 'bpmn_xml'" in missing.error

    wrong = handler.handle(AgentRequest(intent="engineer_process", payload={}))
    assert not wrong.ok and "Unsupported intent" in wrong.error


def test_registered_in_agent_registry():
    registry = AgentRegistry.from_config(AGENTS_CONFIG, handlers={"petri_net_generator": PetriNetGeneratorHandler()})
    agent = registry.get_for_intent(GENERATE_PETRI_NET)
    assert agent is not None and agent.manifest().serves(GENERATE_PETRI_NET)


def test_http_service():
    from agents.petri_net_generator.service import app

    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/agent/manifest").json()["name"] == "petri_net_generator"

    ok = client.post("/generate", json={"input_type": "bpmn_xml", "bpmn_xml": XOR, "output_formats": ["pnml", "dot"]})
    assert ok.status_code == 200
    assert ok.json()["validation"]["soundness"] == "passed" and ok.json()["dot"].startswith("digraph")

    assert client.post("/generate", json={"input_type": "ir", "ir": REQUEST_IR}).status_code == 200
    assert client.post("/generate", json={"input_type": "bpmn_xml"}).status_code == 422  # missing field
    bad = client.post("/generate", json={"input_type": "bpmn_xml", "bpmn_xml": MINIMAL.replace("<task", "<subProcess")})
    assert bad.status_code == 422 and bad.json()["errors"][0]["code"] == "unsupported_bpmn_element"

    contract = client.post("/agent/invoke_sync", json={"intent": GENERATE_PETRI_NET,
                                                        "payload": {"input_type": "bpmn_xml", "bpmn_xml": MINIMAL}})
    assert contract.json()["status"] == "succeeded"
