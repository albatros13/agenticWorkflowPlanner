"""Coordinator routing of 'verify the process against eFLINT' to the eflint_verifier (offline:
the external model-checking service is replaced by a fake of its job API)."""
import pytest

pytest.importorskip("httpx")

from agents.contract import AgentRegistry, InProcessAgent  # noqa: E402
from agents.coordinator import handoff as H  # noqa: E402
from agents.coordinator import intent as I  # noqa: E402
from agents.coordinator.coordinator import Coordinator  # noqa: E402
from agents.coordinator.handoff import ModelHandoff  # noqa: E402
from agents.coordinator.intent import HeuristicIntentClassifier, extract_attachments  # noqa: E402
from agents.coordinator.projects.json_store import JsonProjectStore  # noqa: E402
from agents.coordinator.state.machine import StateMachine  # noqa: E402
from agents.paths import PROJECT_STATES_CONFIG  # noqa: E402

from .eflint_verifier._fake_service import CE, CHECKS, NORMS, PROCESS_PN, RULES, FakeService, make_handler  # noqa: E402

USER = "user@example.com"

MESSAGE = (f"Verify the process against this eFLINT specification:\n```eflint\n{NORMS}```\n"
           f"```qualification\n{RULES}```\n```pn\n{PROCESS_PN}```\n")


def build(tmp_path, service: FakeService, *, with_petri=False) -> Coordinator:
    agents = {"eflint_verifier": InProcessAgent(make_handler(service, tmp_path))}
    intents = {I.VERIFY_EFLINT: "eflint_verifier"}
    if with_petri:
        from agents.petri_net_generator.agent import PetriNetGeneratorHandler

        agents["petri_net_generator"] = InProcessAgent(PetriNetGeneratorHandler(runs_dir=tmp_path / "petri_runs"))
        intents[I.GENERATE_PETRI_NET] = "petri_net_generator"
    return Coordinator(
        registry=AgentRegistry(agents=agents, intent_map=intents),
        store=JsonProjectStore(tmp_path / "projects"),
        machine=StateMachine.from_config(PROJECT_STATES_CONFIG),
        classifier=HeuristicIntentClassifier(),
        handoff=ModelHandoff(tmp_path / "handoff"),
    )


def project_in(coord: Coordinator, state: str) -> str:
    project = coord._create_project(USER, "Case handling", "A case is opened and closed.")
    project.state = state
    coord.store.save(project)
    return project.id


# --- intent + attachments -----------------------------------------------------------------------


@pytest.mark.parametrize("text, slots", [
    ("Verify the process against the eFLINT specification", {}),
    ("Check that the Petri net complies with the regulation within 12 steps", {"max_states": 12}),
    ("Does the workflow violate the norms? Use bound 5.", {"max_states": 5}),
])
def test_heuristic_recognizes_eflint_verification(text, slots):
    result = HeuristicIntentClassifier().classify(text)
    assert result.intent == I.VERIFY_EFLINT and result.slots == slots


def test_plain_verify_and_petri_requests_are_unaffected():
    classify = HeuristicIntentClassifier().classify
    assert classify("please verify the model").intent == I.VERIFY_MODEL
    assert classify("Translate the model into a Petri net").intent == I.GENERATE_PETRI_NET
    assert classify("verify it", context={"attachments": ["eflint"]}).intent == I.VERIFY_EFLINT


def test_attachments_are_split_off_in_code():
    prose, found = extract_attachments(MESSAGE + "\n```\nProperty p Where Globally (Tokens done = 1).\n```")
    assert prose == "Verify the process against this eFLINT specification:"
    assert found == {"eflint": NORMS, "qualification": RULES, "pn": PROCESS_PN,
                     "analysis": "Property p Where Globally (Tokens done = 1).\n"}
    assert extract_attachments("no attachments") == ("no attachments", {})


# --- routing ------------------------------------------------------------------------------------


def test_verification_without_a_project_uses_the_attachments(tmp_path):
    service = FakeService()
    resp = build(tmp_path, service).handle_message(USER, MESSAGE)

    assert resp.intent == I.VERIFY_EFLINT and resp.agent_result.ok
    inputs = service.submitted[0]["inputs"]
    assert inputs["normative"]["source"] == NORMS and inputs["qualification"] == RULES
    assert inputs["physical"]["format"] == "pn"
    assert resp.reply.startswith("Verification PASSED — attached Petri net checked against attached eFLINT")
    assert "no normative violation" in resp.reply and "bounded check" in resp.reply
    assert resp.needs_input is False and resp.project is None


def test_conclusive_verdict_drives_the_lifecycle_and_inputs_are_reused(tmp_path):
    service = FakeService()
    coord = build(tmp_path, service)
    pid = project_in(coord, "MODEL_READY_FOR_VERIFICATION")

    resp = coord.handle_message(USER, MESSAGE + "bound 7", project_id=pid)
    assert resp.state == "VERIFIED" and "now VERIFIED" in resp.reply
    assert service.submitted[0]["options"] == {"max_states": 7}
    history = coord.store.get(USER, pid).history
    assert [h.action for h in history[-2:]] == ["startVerification", "completeVerificationSuccess"]
    assert coord.handoff.load(pid, H.EFLINT) == NORMS  # kept for the next run

    # A second request without attachments reuses the stored inputs (state no longer gated -> ad hoc).
    service.result = CE
    again = coord.handle_message(USER, "verify against the eFLINT spec again", project_id=pid)
    assert again.reply.startswith("Verification FAILED — the project's Petri net checked against the project's")
    assert "begin → finish" in again.reply and again.needs_input is True
    assert again.state == "VERIFIED"  # not in MODEL_READY_FOR_VERIFICATION -> history only
    assert service.submitted[1]["inputs"]["qualification"] == RULES


def test_failed_verdict_moves_to_verification_failed(tmp_path):
    coord = build(tmp_path, FakeService(CE))
    pid = project_in(coord, "MODEL_READY_FOR_VERIFICATION")
    resp = coord.handle_message(USER, MESSAGE + f"```analysis\n{CHECKS}```", project_id=pid)
    assert resp.state == "VERIFICATION_FAILED"
    assert coord.handoff.load(pid, H.ANALYSIS) == CHECKS


def test_service_error_leaves_the_state_alone(tmp_path):
    coord = build(tmp_path, FakeService(fail={"code": "workflow_error", "message": "unknown transition 'x'"}))
    pid = project_in(coord, "MODEL_READY_FOR_VERIFICATION")
    resp = coord.handle_message(USER, MESSAGE, project_id=pid)
    assert resp.state == "MODEL_READY_FOR_VERIFICATION" and resp.needs_input
    assert "could not be completed: workflow_error: unknown transition 'x'" in resp.reply


def test_missing_inputs_are_requested_with_helpful_names(tmp_path):
    service = FakeService({"valid": True})
    coord = build(tmp_path, service)
    resp = coord.handle_message(USER, f"Verify against the eFLINT spec\n```eflint\n{NORMS}```\n```pn\n{PROCESS_PN}```")
    assert resp.needs_input and "qualification rules" in resp.reply
    assert "Process transitions: begin, finish." in resp.reply
    assert "eFLINT acts/events: register." in resp.reply
    assert service.submitted[0]["operation"] == "validate"  # only the name probe ran

    nothing = coord.handle_message(USER, "Verify the process against the eFLINT norms")
    assert nothing.needs_input and "a process model" in nothing.reply and "an eFLINT specification" in nothing.reply


def test_verify_model_delegates_when_the_project_has_an_eflint_spec(tmp_path):
    service = FakeService()
    coord = build(tmp_path, service)
    pid = project_in(coord, "MODEL_READY_FOR_VERIFICATION")
    for name, content in ((H.EFLINT, NORMS), (H.QUALIFICATION, RULES), (H.PETRI_NET_TEXT, PROCESS_PN)):
        coord.handoff.save(pid, name, content)
    resp = coord.handle_message(USER, "please verify the model", project_id=pid)
    assert resp.intent == I.VERIFY_EFLINT and resp.state == "VERIFIED"


def test_petri_net_is_generated_from_the_projects_bpmn_model(tmp_path):
    pytest.importorskip("pm4py")
    from .petri_net_generator._samples import XOR

    service = FakeService()
    coord = build(tmp_path, service, with_petri=True)
    pid = project_in(coord, "MODEL_IN_PROGRESS")
    coord.handoff.save_bpmn(pid, XOR)
    msg = (f"Verify the process against the eFLINT specification\n```eflint\n{NORMS}```\n"
           "```qualification\nSequence\n    approve\nQualify as\n    register.\n```")
    resp = coord.handle_message(USER, msg, project_id=pid)

    assert resp.agent_result.ok, resp.reply
    physical = service.submitted[0]["inputs"]["physical"]
    assert physical["format"] == "pnml" and "finalmarkings" not in physical["source"]
    assert "generated the Petri net from the BPMN model" in resp.reply
    assert "'Approve' → approve" in resp.reply
    assert coord.handoff.load(pid, H.PNML)  # cached for the next run
    assert resp.state == "MODEL_IN_PROGRESS"  # ad-hoc check: no lifecycle change outside READY
