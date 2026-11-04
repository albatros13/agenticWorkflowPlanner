"""Opt-in integration test against the real eFLINT model-checking webservice.

    EFLINT_VERIFIER_LIVE=1 [EFLINT_VERIFIER_URL=http://127.0.0.1:8100] pytest tests/eflint_verifier
"""
import os

import pytest

from agents.contract import AgentRequest
from agents.eflint_verifier.agent import VERIFY_EFLINT, EflintVerifierHandler

from ._fake_service import CHECKS, NORMS, PROCESS_PN, RULES
from .test_pnml import PM4PY_STYLE

pytestmark = pytest.mark.skipif(not os.getenv("EFLINT_VERIFIER_LIVE"), reason="set EFLINT_VERIFIER_LIVE=1")


def test_worked_example_against_the_real_service(tmp_path):
    handler = EflintVerifierHandler(runs_dir=tmp_path)
    payload = {"physical": {"language": "petri-net", "source": PROCESS_PN},
               "normative": {"language": "eflint", "source": NORMS}, "qualification": RULES,
               "analysis": CHECKS, "options": {"max_states": 2}}
    result = handler.handle(AgentRequest(intent=VERIFY_EFLINT, payload=payload))
    assert result.ok, result.error
    outcomes = {p["id"]: p["outcome"] for p in result.output["properties"]}
    assert outcomes == {"completion-recorded": "no_counterexample_within_bound", "never-finish": "counterexample"}

    payload.pop("analysis")
    assert handler.handle(AgentRequest(intent=VERIFY_EFLINT, payload=payload)).output["verdict"] == "passed"


def test_generated_style_pnml_is_accepted(tmp_path):
    result = EflintVerifierHandler(runs_dir=tmp_path).handle(AgentRequest(
        intent=VERIFY_EFLINT, payload={"operation": "validate", "physical": {"language": "petri-net", "source": PM4PY_STYLE}}))
    assert result.ok and result.output["verdict"] == "passed", result.error
