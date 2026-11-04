"""The Petri net viewer demo (agents/petri_net_generator/demo): PNML parsing, token game, DOT, API."""
from __future__ import annotations

import re
import shutil

import pytest

pytest.importorskip("pm4py", reason="PM4Py is not installed: install the `petri_net_generator` extra (`uv sync --extra petri_net_generator`)")

from fastapi.testclient import TestClient  # noqa: E402

from agents.contract import AgentRequest  # noqa: E402
from agents.petri_net_generator.agent import GENERATE_PETRI_NET, PetriNetGeneratorHandler  # noqa: E402
from agents.petri_net_generator.demo.main import app  # noqa: E402
from agents.petri_net_generator.demo.view import PnmlError, build_view, fire, parse_pnml  # noqa: E402
from agents.text_to_bpmn.pipeline.pipeline.artifacts import ArtifactStore  # noqa: E402

from ._samples import XOR  # noqa: E402

# Standard-profile PNML with editor coordinates and no <finalmarkings> (as WoPeD/YAPNE-style tools write).
POSITIONED = """<pnml xmlns="http://www.pnml.org/version-2009/grammar/pnml">
<net id="n" type="http://www.pnml.org/version-2009/grammar/ptnet"><page id="pg">
<place id="p1"><name><text>ready</text></name><graphics><position x="50" y="80"/></graphics><initialMarking><text>2</text></initialMarking></place>
<transition id="t1"><name><text>go</text></name><graphics><position x="150" y="80"/></graphics></transition>
<place id="p2"><name><text>done</text></name><graphics><position x="250" y="80"/></graphics></place>
<arc id="a1" source="p1" target="t1"><inscription><text>2</text></inscription></arc><arc id="a2" source="t1" target="p2"/>
</page></net></pnml>"""


def saved_net() -> str:
    """Generate the XOR net into the (temporary) Petri net storage; return its pnml_ref."""
    result = PetriNetGeneratorHandler().handle(AgentRequest(
        intent=GENERATE_PETRI_NET, payload={"input_type": "bpmn_xml", "bpmn_xml": XOR, "model_name": "Approval"}))
    assert result.ok, result.error
    return result.artifacts_ref


@pytest.fixture
def client():
    return TestClient(app)


# --- view model ---------------------------------------------------------------------------------


def test_token_game_on_a_generated_net_reaches_the_final_marking():
    parsed = parse_pnml((PetriNetGeneratorHandler()._runs() / saved_net() / "net.pnml").read_text())
    view = build_view(parsed, analyze=True)
    assert view["status"] == "running" and view["marking"] == {"source": 1}
    assert view["final_marking"] == {"sink": 1} and view["final_marking_source"] == "pnml"
    assert view["soundness"]["verdict"] == "passed" and view["layout"] == "auto"

    by_label = {t["label"]: t["id"] for t in view["transitions"]}
    m = fire(parsed, None, by_label["Validate"])
    assert {t["label"] for t in build_view(parsed, m)["transitions"] if t["enabled"]} == {"Approve", "Reject"}
    m = fire(parsed, m, by_label["Reject"])
    while (v := build_view(parsed, m))["status"] == "running":   # the closing silent transition
        m = fire(parsed, m, v["enabled"][0])
    assert v["status"] == "final" and v["marking"] == {"sink": 1}

    with pytest.raises(PnmlError, match="not enabled"):
        fire(parsed, None, by_label["Approve"])


def test_pnml_positions_are_honoured_and_an_inferred_final_marking_is_flagged():
    parsed = parse_pnml(POSITIONED)
    view = build_view(parsed, analyze=True)
    assert view["layout"] == "pnml" and view["marking"] == {"p1": 2}
    assert view["final_marking_source"] == "inferred"            # PM4Py inferred it: no <finalmarkings>
    assert "inferred" in view["soundness"]["detail"]
    assert 'pos="50,-80!"' in view["dot"] and "layout=neato" in view["dot"]
    assert '[label="2"]' in view["dot"]                           # arc weight shown
    assert {p["label"] for p in view["places"]} == {"ready", "done"}
    assert fire(parsed, None, "t1") == {"p2": 1}


def test_dot_uses_stable_svg_ids_and_highlights_enabled_transitions():
    view = build_view(parse_pnml(POSITIONED))
    t = view["transitions"][0]
    assert t["svg_id"] == "t0" and t["enabled"]
    assert 'id="t0"' in view["dot"] and "#dcfce7" in view["dot"]   # enabled -> green fill


@pytest.mark.parametrize("doc, message", [
    ("<pnml><net", "well-formed"),
    ('<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><pnml>&e;</pnml>', "DOCTYPE"),
    ("<bpmn/>", "expected <pnml>"),
])
def test_bad_documents_are_rejected(doc, message):
    with pytest.raises(PnmlError, match=message):
        parse_pnml(doc)


# --- HTTP API -----------------------------------------------------------------------------------


def test_saved_nets_are_listed_viewed_and_fired_by_ref(client):
    ref = saved_net()
    nets = client.get("/api/nets").json()["nets"]
    assert [n["ref"] for n in nets] == [ref] and nets[0]["name"] == "Approval" and nets[0]["soundness"] == "passed"

    view = client.post("/api/view", json={"ref": ref}).json()
    assert view["name"] == "Approval" and view["status"] == "running" and "soundness" in view
    validate = next(t["id"] for t in view["transitions"] if t["label"] == "Validate")
    fired = client.post("/api/fire", json={"ref": ref, "marking": view["marking"], "transition": validate}).json()
    assert sorted(t["label"] for t in fired["transitions"] if t["enabled"]) == ["Approve", "Reject"]
    assert "soundness" not in fired                               # analysis only on open

    assert client.get(f"/api/nets/{ref}/pnml").text.lstrip().startswith("<?xml")


def test_uploaded_pnml_and_errors(client):
    assert client.post("/api/view", json={"pnml": POSITIONED}).json()["layout"] == "pnml"
    assert client.post("/api/fire", json={"pnml": POSITIONED, "transition": "nope"}).status_code == 422
    assert client.post("/api/view", json={"pnml": "<pnml><net"}).status_code == 422
    assert client.post("/api/view", json={}).status_code == 422                  # neither pnml nor ref
    assert client.post("/api/view", json={"ref": "missing"}).status_code == 404
    assert client.get("/api/nets/bad%2E%2E/pnml").status_code == 400
    assert client.get("/").status_code == 200


def test_a_text_to_bpmn_run_is_converted_and_listed(client, tmp_path, monkeypatch):
    runs = tmp_path / "text_to_bpmn_runs"
    ArtifactStore.new_run(runs, "run1").save("bpmn", XOR)
    monkeypatch.setenv("RUNS_DIR", str(runs))
    assert [r["run_id"] for r in client.get("/api/bpmn_runs").json()["runs"]] == ["run1"]
    ref = client.post("/api/convert", json={"run_id": "run1"}).json()["ref"]
    assert any(n["ref"] == ref for n in client.get("/api/nets").json()["nets"])
    assert client.post("/api/convert", json={"run_id": "nope"}).status_code == 404


@pytest.mark.skipif(shutil.which("dot") is None, reason="Graphviz 'dot' binary not installed")
def test_server_side_svg_fallback(client):
    view = client.post("/api/view", json={"pnml": POSITIONED}).json()
    svg = client.post("/api/svg", json={"dot": view["dot"]})   # pinned layout; falls back if neato is missing
    assert svg.status_code == 200 and "<svg" in svg.text
    assert re.search(r'<g id="t0" class="node"', svg.text)
