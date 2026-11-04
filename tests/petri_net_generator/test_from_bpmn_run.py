"""``python -m agents.petri_net_generator.from_bpmn_run``: text_to_bpmn storage run -> Petri net.

Runs are built in a temporary folder with text_to_bpmn's own ArtifactStore, so the tests do not
depend on the (gitignored, machine-specific) runs in agents/text_to_bpmn/storage.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pm4py", reason="PM4Py is not installed: install the `petri_net_generator` extra (`uv sync --extra petri_net_generator`)")

from agents.petri_net_generator.agent import PetriNetGeneratorHandler  # noqa: E402
from agents.petri_net_generator.from_bpmn_run import convert_run, list_runs, main  # noqa: E402
from agents.petri_net_generator.net_validation import visible_traces  # noqa: E402
from agents.text_to_bpmn.pipeline.pipeline.artifacts import ArtifactStore  # noqa: E402

from .._ir_fixtures import xor_threshold  # noqa: E402
from ._samples import XOR, bpmn, flow  # noqa: E402

UNSUPPORTED = bpmn('<startEvent id="S"/><intermediateThrowEvent id="I"/><endEvent id="E"/>'
                   + flow("f1", "S", "I") + flow("f2", "I", "E"))


@pytest.fixture
def runs(tmp_path, monkeypatch):
    """A text_to_bpmn runs folder with a good run (BPMN + IR), an unsupported one, and an empty one."""
    root = tmp_path / "text_to_bpmn_runs"
    good = ArtifactStore.new_run(root, "run_good")
    good.save("ir", xor_threshold())
    good.save("bpmn", XOR)
    ArtifactStore.new_run(root, "run_unsupported").save("bpmn", UNSUPPORTED)
    (root / "run_empty").mkdir()  # e.g. a run whose pipeline stopped before compilation
    monkeypatch.setenv("RUNS_DIR", str(root))
    monkeypatch.setenv("PETRI_NET_RUNS_DIR", str(tmp_path / "petri_runs"))
    return root


def test_convert_run_from_bpmn_xml(runs):
    result = convert_run(runs / "run_good", handler=PetriNetGeneratorHandler(runs_dir=runs.parent / "pn"))
    out = result["output"]
    assert result["status"] == "succeeded" and out["status"] == "succeeded"
    assert out["input_type"] == "bpmn_xml"
    assert out["validation"]["workflow_net"] and out["validation"]["soundness"] == "passed"
    assert out["process_name"] == xor_threshold().name  # model name taken from the run's IR
    assert out["pnml"].lstrip().startswith("<?xml") and (runs.parent / "pn" / out["pnml_ref"] / "net.pnml").exists()


def test_convert_run_from_graph_ir(runs):
    out = convert_run(runs / "run_good", source="ir")["output"]
    assert out["status"] == "succeeded" and out["input_type"] == "text_to_bpmn_ir"
    assert out["net_summary"]["transitions"] >= 2


def test_unsupported_bpmn_is_a_structured_failure(runs):
    result = convert_run(runs / "run_unsupported")
    assert result["status"] == "failed" and "intermediateThrowEvent" in result["error"]


def test_list_runs_only_shows_runs_with_a_bpmn_model(runs):
    rows = list_runs(runs)
    assert {r["run_id"] for r in rows} == {"run_good", "run_unsupported"}
    assert next(r for r in rows if r["run_id"] == "run_good")["process"] == xor_threshold().name


def test_cli_converts_a_run_and_writes_pnml_and_dot(runs, tmp_path, capsys):
    out_dir = tmp_path / "nets"
    assert main(["run_good", "--out", str(out_dir), "--dot"]) == 0
    printed = capsys.readouterr().out
    assert "run_good: OK" in printed and "soundness passed" in printed
    pnml = (out_dir / "run_good.pnml").read_text()
    assert "<pnml" in pnml and (out_dir / "run_good.dot").read_text().startswith("digraph")
    # behaviour, not just shape: the net's visible traces are the two XOR branches
    from pm4py.objects.petri_net.importer.variants.pnml import import_net_from_string

    net, im, fm = import_net_from_string(pnml.encode())
    assert visible_traces(net, im, fm) == {("Validate", "Approve"), ("Validate", "Reject")}


def test_cli_run_by_path_listing_and_exit_codes(runs, capsys):
    assert main([str(runs / "run_good"), "--no-soundness"]) == 0      # a path works as well as an id
    assert main([]) == 0                                              # no arguments: list the runs
    assert "run_good" in capsys.readouterr().out
    assert main(["--all"]) == 1                                       # one of the two runs fails
    assert "run_unsupported: FAILED" in capsys.readouterr().out
    assert main(["no_such_run"]) == 2
    assert main(["run_empty"]) == 2                                   # run without bpmn.json
    assert "no bpmn.json artifact" in capsys.readouterr().err
