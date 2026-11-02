"""Demo: natural-language text -> text_to_bpmn agent -> Petri net generator -> PNML.

Runs fully offline: the text_to_bpmn agent uses the scripted LLM from ``tests/_bpmn_fixtures``
(the BPH follow-up process: assess IPSS, then *either* continue treatment *or* refer back).
Both hand-off routes are exercised:

* the BPMN 2.0 XML the text_to_bpmn agent returns (``output.bpmn_xml``), and
* the graph IR it stores for the run (``runs/<run_id>/ir.json``).

Run with ``pytest tests/petri_net_generator/test_text_to_bpmn_to_petri_net.py -s`` to print the
resulting net and the files written for inspection in an external Petri-net tool.
"""
from __future__ import annotations

import json

import pytest
from pm4py.objects.petri_net.importer.variants.pnml import import_net_from_string

from agents.contract import AgentRequest
from agents.petri_net_generator.agent import GENERATE_PETRI_NET, PetriNetGeneratorHandler
from agents.petri_net_generator.net_validation import visible_traces
from agents.text_to_bpmn.agent import ENGINEER_PROCESS, TextToBpmnHandler
from tests._bpmn_fixtures import TEXT, scripted_provider

EXPECTED_TRACES = {
    ("Assess IPSS", "Evaluate Improved >= 25%?", "Continue treatment"),
    ("Assess IPSS", "Evaluate Improved >= 25%?", "Refer back to urologist"),
}


@pytest.fixture
def text_to_bpmn_result(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "bpmn_runs"))
    result = TextToBpmnHandler(provider=scripted_provider()).handle(
        AgentRequest(intent=ENGINEER_PROCESS, payload={"text": TEXT, "process_name": "Follow-up"}))
    assert result.ok, result.error
    return result, tmp_path / "bpmn_runs" / result.artifacts_ref


def _generate(payload: dict, tmp_path):
    result = PetriNetGeneratorHandler(runs_dir=tmp_path / "petri_runs").handle(
        AgentRequest(intent=GENERATE_PETRI_NET, payload={**payload, "output_formats": ["pnml", "dot"]}))
    assert result.ok, result.error
    return result.output


def _assert_follow_up_net(out: dict) -> None:
    assert out["status"] == "succeeded"
    assert out["validation"]["structurally_valid"] and out["validation"]["workflow_net"]
    assert out["validation"]["soundness"] == "passed"
    assert out["initial_marking"] == {"source": 1} and out["final_marking"] == {"sink": 1}
    # Behaviour, not just shape: the XOR decision yields exactly two alternative runs.
    net, im, fm = import_net_from_string(out["pnml"].encode())
    assert visible_traces(net, im, fm) == EXPECTED_TRACES


def _show(title: str, out: dict, tmp_path) -> None:
    (tmp_path / "follow_up.pnml").write_text(out["pnml"], encoding="utf-8")
    (tmp_path / "follow_up.dot").write_text(out["dot"], encoding="utf-8")
    s = out["net_summary"]
    print(f"\n=== {title}: {out['process_name']} ===")
    print(f"places={s['places']} transitions={s['transitions']} (silent {s['silent_transitions']}) "
          f"arcs={s['arcs']}  source={s['source_place']} sink={s['sink_place']}")
    print(f"soundness: {out['validation']['soundness']} — {out['validation']['soundness_detail']}")
    print(f"PNML: {tmp_path / 'follow_up.pnml'}\nDOT:  {tmp_path / 'follow_up.dot'}")
    print(out["pnml"])


def test_petri_net_from_text_to_bpmn_bpmn_xml(text_to_bpmn_result, tmp_path):
    bpmn_result, _ = text_to_bpmn_result
    out = _generate({"input_type": "bpmn_xml", "bpmn_xml": bpmn_result.output["bpmn_xml"]}, tmp_path)
    _assert_follow_up_net(out)
    assert out["warnings"] == []
    _show("BPMN XML from text_to_bpmn -> Petri net", out, tmp_path)


def test_petri_net_from_text_to_bpmn_ir(text_to_bpmn_result, tmp_path):
    _, run_dir = text_to_bpmn_result
    ir = json.loads((run_dir / "ir.json").read_text(encoding="utf-8"))["data"]
    out = _generate({"input_type": "text_to_bpmn_ir", "text_to_bpmn_ir": ir}, tmp_path)
    _assert_follow_up_net(out)
    assert (tmp_path / "petri_runs" / out["pnml_ref"] / "net.pnml").read_text(encoding="utf-8") == out["pnml"]
