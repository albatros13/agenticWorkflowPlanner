"""The eflint_verifier handler against a fake of the external service (offline)."""
import json

import pytest

from agents.contract import AgentRequest
from agents.eflint_verifier.agent import (VERIFY_EFLINT, EflintVerifierHandler, infer_operation,
                                          verdict_of)

from ._fake_service import CE, CHECKS, NORMS, PROCESS_PN, RULES, FakeService, make_handler
from .test_pnml import PM4PY_STYLE

COMPOSITION = {"physical": {"language": "petri-net", "source": PROCESS_PN},
               "normative": {"language": "eflint", "source": NORMS}, "qualification": RULES}


def invoke(handler, payload, intent=VERIFY_EFLINT):
    return handler.handle(AgentRequest(intent=intent, payload=payload, correlation_id="c1"))


def test_manifest_advertises_verify_eflint():
    m = EflintVerifierHandler().manifest()
    assert m.name == "eflint_verifier" and m.serves(VERIFY_EFLINT)


def test_without_analysis_a_compliance_check_over_all_transitions_is_generated(tmp_path):
    service = FakeService(polls_until_done=3)
    result = invoke(make_handler(service, tmp_path), COMPOSITION)

    assert result.ok, result.error
    sent = service.submitted[0]
    assert sent["operation"] == "compliance-check"
    assert sent["inputs"]["physical"] == {"language": "petri-net", "format": "pn", "source": PROCESS_PN,
                                          "dependencies": {}}
    assert sent["inputs"]["qualification"] == RULES
    assert "Compliance-checking With Physical Actions = begin, finish." in sent["inputs"]["analysis"]
    assert sent["options"] == {"max_states": 30}
    out = result.output
    assert out["verdict"] == "passed" and out["analysis_generated"] is True
    assert out["summary"] == "'Compliance' holds within bound 30"
    # polled until terminal, fetched the result, then deleted the job
    assert service.calls[-2:] == [("GET", "/v1/jobs/job-1/result"), ("DELETE", "/v1/jobs/job-1")]
    report = json.loads((tmp_path / "verifier_runs" / result.artifacts_ref / "report.json").read_text())
    assert report["verdict"] == "passed"


def test_counterexample_gives_failed_verdict_with_witness(tmp_path):
    service = FakeService(CE)
    result = invoke(make_handler(service, tmp_path), {**COMPOSITION, "analysis": CHECKS, "options": {"max_states": 2}})

    assert result.ok  # the analysis completed; its answer is the counterexample
    assert service.submitted[0]["operation"] == "model-check"
    assert service.submitted[0]["options"] == {"max_states": 2}
    assert result.output["verdict"] == "failed"
    assert result.output["properties"][0]["witness"] == [["begin"], ["finish"]]
    assert "VIOLATED" in result.output["summary"] and "begin → finish" in result.output["summary"]


def test_generated_pnml_is_normalised_before_submission(tmp_path):
    service = FakeService()
    result = invoke(make_handler(service, tmp_path), {"physical": {"language": "petri-net", "source": PM4PY_STYLE}})

    sent = service.submitted[0]["inputs"]
    assert service.submitted[0]["inputs"]["physical"]["format"] == "pnml"
    assert "finalmarkings" not in sent["physical"]["source"]
    assert "submit-request-b, submit-request-c, submit-request, g2_" in sent["analysis"]
    assert result.output["renamed"]["Submit request"] == "submit-request-b"


def test_failed_job_is_a_failed_result_with_the_service_error(tmp_path):
    service = FakeService(fail={"code": "workflow_error", "message": "unknown Petri-net transition 'nope'"})
    result = invoke(make_handler(service, tmp_path), COMPOSITION)
    assert not result.ok
    assert result.error == "workflow_error: unknown Petri-net transition 'nope'"
    assert result.artifacts_ref  # recorded anyway


def test_rejected_request_and_unreachable_service(tmp_path):
    result = invoke(make_handler(FakeService(reject="operation: unknown variant"), tmp_path), COMPOSITION)
    assert not result.ok and result.error.startswith("invalid_request: operation: unknown variant")

    from agents.eflint_verifier.client import EflintVerifierClient
    from agents.eflint_verifier.config import VerifierSettings
    import httpx

    def refuse(request):
        raise httpx.ConnectError("Connection refused", request=request)

    handler = EflintVerifierHandler(client=EflintVerifierClient(VerifierSettings(base_url="http://down"),
                                                                transport=httpx.MockTransport(refuse)),
                                    runs_dir=tmp_path / "runs")
    result = invoke(handler, COMPOSITION)
    assert not result.ok and result.error.startswith("unavailable:") and "http://down" in result.error


def test_job_exceeding_the_wait_timeout_is_cancelled(tmp_path):
    service = FakeService(polls_until_done=None)
    result = invoke(make_handler(service, tmp_path, wait_timeout_s=0.0), COMPOSITION)
    assert not result.ok and "wait_timeout" in result.error
    assert ("POST", "/v1/jobs/job-1/cancel") in service.calls


@pytest.mark.parametrize("payload, message", [
    ({}, "physical"),
    ({"physical": {"language": "eflint", "source": NORMS}, "qualification": RULES}, "together"),
    ({**COMPOSITION, "bogus": 1}, "bogus"),
])
def test_invalid_payloads(tmp_path, payload, message):
    result = invoke(make_handler(FakeService(), tmp_path), payload)
    assert not result.ok and message in result.error


def test_plan_without_analysis_is_refused_and_wrong_intent(tmp_path):
    handler = make_handler(FakeService(), tmp_path)
    assert "analysis document is required" in invoke(handler, {**COMPOSITION, "operation": "plan"}).error
    assert "Unsupported intent" in invoke(handler, COMPOSITION, intent="generate_eflint").error


def test_operation_inference_and_verdicts():
    assert infer_operation(None) == "compliance-check"
    assert infer_operation("// c\nPlan p Where Goal = Tokens done = 1.") == "plan"
    assert infer_operation("Compliance-checking With Physical Actions = a.\nProperty p Where Globally (Tokens a = 1).") == "model-check"
    assert verdict_of("plan", {"results": [{"outcome": "plan_found"}]}) == "passed"
    assert verdict_of("plan", {"results": [{"outcome": "no_plan_within_bound"}]}) == "failed"
    assert verdict_of("model-check", {"results": [{"outcome": "infeasible_configuration"}]}) == "inconclusive"
    assert verdict_of("validate", {"valid": True}) == "passed"
