"""Opt-in integration test against the real eFLINT model-checking webservice.

    EFLINT_VERIFIER_LIVE=1 [EFLINT_VERIFIER_URL=http://127.0.0.1:8100] uv run pytest tests/eflint_verifier -v

Run records go to pytest's temporary directory. Add ``EFLINT_VERIFIER_KEEP_RUNS=1`` to write them
to the agent's storage instead (``agents/eflint_verifier/storage/runs/``, or
``EFLINT_VERIFIER_RUNS_DIR``); add ``-s`` to print where each run was saved.
"""
import os

import pytest

from ._deps import require_deps

require_deps()

from agents.contract import AgentRequest
from agents.eflint_verifier.agent import VERIFY_EFLINT, EflintVerifierHandler

from ._fake_service import CHECKS, NORMS, PROCESS_PN, RULES
from .test_pnml import PM4PY_STYLE

pytestmark = pytest.mark.skipif(not os.getenv("EFLINT_VERIFIER_LIVE"),
                                reason="live test against the real eFLINT service: set EFLINT_VERIFIER_LIVE=1")


def make_handler(tmp_path) -> EflintVerifierHandler:
    """Agent storage (or EFLINT_VERIFIER_RUNS_DIR) with EFLINT_VERIFIER_KEEP_RUNS=1, else tmp_path."""
    return EflintVerifierHandler(runs_dir=None if os.getenv("EFLINT_VERIFIER_KEEP_RUNS") else tmp_path)


def saved(handler: EflintVerifierHandler, result) -> None:
    run = handler._runs() / result.artifacts_ref
    assert (run / "request.json").exists() and (run / "report.json").exists()
    print(f"\n  run saved to {run}")


def test_worked_example_against_the_real_service(tmp_path):
    handler = make_handler(tmp_path)
    payload = {"physical": {"language": "petri-net", "source": PROCESS_PN},
               "normative": {"language": "eflint", "source": NORMS}, "qualification": RULES,
               "analysis": CHECKS, "options": {"max_states": 2}}
    result = handler.handle(AgentRequest(intent=VERIFY_EFLINT, payload=payload))
    assert result.ok, result.error
    outcomes = {p["id"]: p["outcome"] for p in result.output["properties"]}
    assert outcomes == {"completion-recorded": "no_counterexample_within_bound", "never-finish": "counterexample"}
    saved(handler, result)

    payload.pop("analysis")
    result = handler.handle(AgentRequest(intent=VERIFY_EFLINT, payload=payload))
    assert result.ok and result.output["verdict"] == "passed", result.error
    saved(handler, result)


def test_generated_style_pnml_is_accepted(tmp_path):
    handler = make_handler(tmp_path)
    result = handler.handle(AgentRequest(
        intent=VERIFY_EFLINT, payload={"operation": "validate", "physical": {"language": "petri-net", "source": PM4PY_STYLE}}))
    assert result.ok and result.output["verdict"] == "passed", result.error
    saved(handler, result)
