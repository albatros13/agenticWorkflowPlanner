"""Coordinator routing of BPMN/text -> Petri net requests to the Petri net generator (offline)."""
import pytest

pytest.importorskip("pm4py")  # the worker needs the `petri_net` extra

from agents.contract import AgentRegistry, InProcessAgent  # noqa: E402
from agents.coordinator import intent as I  # noqa: E402
from agents.coordinator.coordinator import Coordinator  # noqa: E402
from agents.coordinator.handoff import ModelHandoff  # noqa: E402
from agents.coordinator.intent import HeuristicIntentClassifier, extract_bpmn_xml  # noqa: E402
from agents.coordinator.projects.json_store import JsonProjectStore  # noqa: E402
from agents.coordinator.state.machine import StateMachine  # noqa: E402
from agents.paths import PROJECT_STATES_CONFIG  # noqa: E402
from agents.petri_net_generator.agent import PetriNetGeneratorHandler  # noqa: E402
from agents.petri_net_generator.text_to_ir import TASK  # noqa: E402
from agents.text_to_bpmn.agent import TextToBpmnHandler  # noqa: E402
from agents.text_to_bpmn.pipeline.llm.mock import ScriptedLLMProvider  # noqa: E402

from ._bpmn_fixtures import TEXT, scripted_provider  # noqa: E402
from .petri_net_generator._samples import MINIMAL, XOR, act, ir  # noqa: E402

USER = "user@example.com"

REQUEST_IR = ir({"kind": "sequence", "steps": [
    act("submit", "Submit request"),
    {"kind": "xor", "branches": [{"condition": "valid", "body": act("approve", "Approve")},
                                 {"condition": "invalid", "body": act("reject", "Reject")}]},
]})


def build(tmp_path, *, petri_provider=None, with_petri=True) -> Coordinator:
    agents = {"text_to_bpmn": InProcessAgent(TextToBpmnHandler(provider=scripted_provider()))}
    intents = {"engineer_process": "text_to_bpmn"}
    if with_petri:
        handler = PetriNetGeneratorHandler(provider=petri_provider, runs_dir=tmp_path / "petri_runs")
        agents["petri_net_generator"] = InProcessAgent(handler)
        intents[I.GENERATE_PETRI_NET] = "petri_net_generator"
    return Coordinator(
        registry=AgentRegistry(agents=agents, intent_map=intents),
        store=JsonProjectStore(tmp_path / "projects"),
        machine=StateMachine.from_config(PROJECT_STATES_CONFIG),
        classifier=HeuristicIntentClassifier(),
        handoff=ModelHandoff(tmp_path / "handoff"),
    )


@pytest.fixture(autouse=True)
def _runs(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))


# --- intent recognition ------------------------------------------------------------------------


@pytest.mark.parametrize("text, description", [
    ("Translate the model into a Petri net", None),
    ("Export the workflow as PNML", None),
    ("Make a Petri net for: A request is submitted, then validated.", "A request is submitted, then validated."),
    # A description that would otherwise hit the 'list' / 'status' / 'process' rules.
    ("Petri net please: list the items, check the status, then process the order.",
     "list the items, check the status, then process the order."),
])
def test_heuristic_recognizes_petri_net_requests(text, description):
    result = HeuristicIntentClassifier().classify(text)
    assert result.intent == I.GENERATE_PETRI_NET
    assert result.slots.get("process_description") == description


def test_other_intents_are_unaffected():
    classify = HeuristicIntentClassifier().classify
    assert classify("Generate a BPMN diagram for the intake process").intent == I.ENGINEER_PROCESS
    assert classify("list my projects").intent == I.LIST_PROJECTS


def test_bpmn_xml_is_split_off_the_message():
    prose, xml = extract_bpmn_xml(f"Convert this to a Petri net:\n{XOR}\nThanks")
    assert xml == XOR and prose == "Convert this to a Petri net:\n\nThanks"
    assert extract_bpmn_xml("no model here") == ("no model here", None)


# --- routing -----------------------------------------------------------------------------------


def test_attached_bpmn_is_forwarded_without_a_project(tmp_path):
    c = build(tmp_path)
    resp = c.handle_message(USER, f"Please convert this BPMN to a Petri net:\n{XOR}")
    assert resp.intent == I.GENERATE_PETRI_NET and resp.project is None
    assert resp.agent_result.ok and resp.agent_result.agent == "petri_net_generator"
    out = resp.agent_result.output
    assert out["input_type"] == "bpmn_xml" and out["validation"]["soundness"] == "passed"
    assert out["pnml"].startswith("<?xml")
    assert "attached BPMN model" in resp.reply and "soundness check passed" in resp.reply


def test_projects_bpmn_model_is_translated_without_changing_state(tmp_path):
    c = build(tmp_path)
    engineered = c.handle_message(USER, TEXT)
    pid = engineered.project.id
    assert c.handoff.load_bpmn(pid) == engineered.agent_result.output["bpmn_xml"]

    resp = c.handle_message(USER, "Translate the model into a Petri net", project_id=pid)
    assert resp.agent_result.ok, resp.agent_result.error
    assert resp.agent_result.output["input_type"] == "bpmn_xml"
    assert set(resp.agent_result.output["net_summary"]["visible_labels"]) >= {
        "Assess IPSS", "Continue treatment", "Refer back to urologist"}
    assert "BPMN model of" in resp.reply
    assert resp.state == engineered.state == "MODEL_IN_PROGRESS"  # derived view: no lifecycle move

    project = c.store.get(USER, pid)
    assert project.history[-1].intent == I.GENERATE_PETRI_NET
    assert project.history[-1].agent == "petri_net_generator"
    assert resp.agent_result.artifacts_ref in project.runs

    c.handle_message(USER, "delete this project", project_id=pid)
    assert c.handoff.load_bpmn(pid) is None


def test_description_in_message_uses_text_path(tmp_path):
    provider = ScriptedLLMProvider({TASK: REQUEST_IR})
    c = build(tmp_path, petri_provider=provider)
    resp = c.handle_message(USER, "Make a Petri net for: A request is submitted. If valid approve, else reject.")
    assert resp.agent_result.ok
    assert resp.agent_result.output["input_type"] == "text"
    assert "A request is submitted. If valid approve, else reject." in provider.calls[0].prompt
    assert "Make a Petri net" not in provider.calls[0].prompt


def test_blocking_ambiguity_is_relayed_as_a_question(tmp_path):
    ambiguous = {**REQUEST_IR, "ambiguities": [{"severity": "blocking", "message": "unclear",
                                                 "question": "Are approve and reject alternatives?"}]}
    c = build(tmp_path, petri_provider=ScriptedLLMProvider({TASK: ambiguous}))
    resp = c.handle_message(USER, "Petri net for: submit, approve and reject")
    assert resp.needs_input and resp.agent_result.output["status"] == "needs_clarification"
    assert "Are approve and reject alternatives?" in resp.reply


def test_unsupported_bpmn_is_reported(tmp_path):
    resp = build(tmp_path).handle_message(USER, "Petri net: " + MINIMAL.replace("<task", "<subProcess"))
    assert resp.needs_input and not resp.agent_result.ok
    assert "unsupported_bpmn_element" in resp.reply


def test_missing_source_asks_for_input(tmp_path):
    resp = build(tmp_path).handle_message(USER, "Translate to a Petri net")
    assert resp.needs_input and resp.agent_result is None
    assert "paste a BPMN 2.0 model" in resp.reply


def test_worker_not_available(tmp_path):
    resp = build(tmp_path, with_petri=False).handle_message(USER, f"Petri net: {XOR}")
    assert resp.needs_input and "not available" in resp.reply
