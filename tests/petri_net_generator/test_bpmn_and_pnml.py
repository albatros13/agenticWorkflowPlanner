"""BPMN XML -> Petri net via PM4Py, input hardening, PNML round-trips, and net validation."""
from __future__ import annotations

import pytest
from lxml import etree
from pm4py.objects.petri_net.importer.variants.pnml import import_net_from_string
from pm4py.objects.petri_net.obj import Marking, PetriNet
from pm4py.objects.petri_net.utils.petri_utils import add_arc_from_to

from agents.petri_net_generator.bpmn_converter import BpmnConversionError, bpmn_to_petri_net
from agents.petri_net_generator.exporters import to_dot, to_pnml
from agents.petri_net_generator.net_validation import (
    check_soundness,
    marking_dict,
    structural_findings,
    visible_traces,
)

from ._samples import AND, MINIMAL, XOR, bpmn, flow

LIMIT = 1_000_000


def convert(xml: str):
    net, im, fm, findings, _ = bpmn_to_petri_net(xml, max_bytes=LIMIT)
    return net, im, fm, findings


# --- BPMN path ---------------------------------------------------------------------------------


def test_minimal_start_task_end():
    net, im, fm, findings = convert(MINIMAL)
    assert findings == []
    assert structural_findings(net, im, fm) == []
    assert visible_traces(net, im, fm) == {("Do work",)}
    assert check_soundness(net, im, fm, max_states=1000, timeout_s=5)[0] == "passed"


def test_exclusive_gateway_branches_are_alternatives():
    net, im, fm, _ = convert(XOR)
    assert visible_traces(net, im, fm) == {("Validate", "Approve"), ("Validate", "Reject")}
    assert check_soundness(net, im, fm, max_states=1000, timeout_s=5)[0] == "passed"


def test_parallel_split_join_synchronizes():
    net, im, fm, _ = convert(AND)
    assert visible_traces(net, im, fm) == {("X-ray", "Bone scan", "Discuss"), ("Bone scan", "X-ray", "Discuss")}
    assert check_soundness(net, im, fm, max_states=1000, timeout_s=5)[0] == "passed"


def test_conversion_ids_are_deterministic():
    assert to_pnml(*convert(AND)[:3]) == to_pnml(*convert(AND)[:3])


@pytest.mark.parametrize("element, code", [
    ('<subProcess id="SP"/>', "unsupported_bpmn_element"),
    ('<boundaryEvent id="B" attachedToRef="A"/>', "unsupported_bpmn_element"),
    ('<intermediateCatchEvent id="I"/>', "unsupported_bpmn_element"),
    ('<eventBasedGateway id="EG"/>', "unsupported_bpmn_element"),
])
def test_unsupported_elements_are_errors_not_silently_dropped(element, code):
    xml = bpmn('<startEvent id="S"/><task id="A" name="A"/><endEvent id="E"/>' + element
               + flow("f1", "S", "A") + flow("f2", "A", "E"))
    with pytest.raises(BpmnConversionError) as exc:
        convert(xml)
    assert code in {f.code for f in exc.value.findings}


def test_inclusive_gateway_is_flagged_as_approximated():
    xml = bpmn('<startEvent id="S"/><inclusiveGateway id="G1"/><task id="A" name="A"/>'
               '<task id="B" name="B"/><inclusiveGateway id="G2"/><endEvent id="E"/>'
               + flow("f1", "S", "G1") + flow("f2", "G1", "A") + flow("f3", "G1", "B")
               + flow("f4", "A", "G2") + flow("f5", "B", "G2") + flow("f6", "G2", "E"))
    *_, findings = convert(xml)
    assert [f.code for f in findings] == ["inclusive_gateway_approximated"]


def test_missing_start_and_end_events():
    with pytest.raises(BpmnConversionError) as exc:
        convert(bpmn('<task id="A" name="A"/>'))
    assert {"missing_start_event", "missing_end_event"} <= {f.code for f in exc.value.findings}


@pytest.mark.parametrize("xml, code", [
    ("<definitions><process>", "malformed_xml"),
    ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
     f'<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL">&x;</definitions>', "dtd_not_allowed"),
    ("<notbpmn/>", "not_bpmn"),
])
def test_unsafe_or_invalid_xml_is_rejected(xml, code):
    with pytest.raises(BpmnConversionError) as exc:
        convert(xml)
    assert [f.code for f in exc.value.findings] == [code]
    assert "passwd" not in str(exc.value)


def test_oversized_payload_is_rejected():
    with pytest.raises(BpmnConversionError) as exc:
        bpmn_to_petri_net(MINIMAL, max_bytes=100)
    assert exc.value.findings[0].code == "input_too_large"


# --- PNML export -------------------------------------------------------------------------------


@pytest.mark.parametrize("xml", [MINIMAL, XOR, AND])
def test_pnml_round_trips_through_pm4py(xml):
    net, im, fm, _ = convert(xml)
    pnml = to_pnml(net, im, fm)
    root = etree.fromstring(pnml.encode())  # well-formed
    assert root.tag == "pnml"

    net2, im2, fm2 = import_net_from_string(pnml.encode())
    assert sorted(p.name for p in net2.places) == sorted(p.name for p in net.places)
    assert sorted((t.name, t.label) for t in net2.transitions) == sorted((t.name, t.label) for t in net.transitions)
    assert sorted((a.source.name, a.target.name) for a in net2.arcs) == \
        sorted((a.source.name, a.target.name) for a in net.arcs)
    assert marking_dict(im2) == marking_dict(im) == {"source": 1}
    assert marking_dict(fm2) == marking_dict(fm) == {"sink": 1}
    assert visible_traces(net2, im2, fm2) == visible_traces(net, im, fm)


def test_dot_preview_lists_all_nodes():
    net, im, fm, _ = convert(XOR)
    dot = to_dot(net, im, fm)
    assert dot.startswith("digraph") and '"Approve"' in dot and dot.count("->") == len(net.arcs)


# --- structural validation vs soundness --------------------------------------------------------


def _net(*arcs: tuple[str, str]):
    """Build a net from (src, dst) arcs; names starting with 't' are transitions."""
    net, nodes = PetriNet("n"), {}
    for name in {n for arc in arcs for n in arc}:
        nodes[name] = PetriNet.Transition(name, name) if name.startswith("t") else PetriNet.Place(name)
        (net.transitions if name.startswith("t") else net.places).add(nodes[name])
    for src, dst in arcs:
        add_arc_from_to(nodes[src], nodes[dst], net)
    return net, nodes


def test_invalid_marking_and_dangling_arc_are_caught():
    net, nodes = _net(("source", "t1"), ("t1", "sink"))
    stray = PetriNet.Place("ghost")
    add_arc_from_to(stray, nodes["t1"], net)  # arc to a place that is not in the net
    net.places.discard(stray)
    codes = {f.code for f in structural_findings(net, Marking({stray: 1}), Marking({nodes["sink"]: 1}))}
    assert {"dangling_arc", "invalid_initial_marking"} <= codes


def test_dead_end_and_disconnected_nodes_are_reported():
    net, nodes = _net(("source", "t1"), ("t1", "sink"), ("t1", "p_dead"), ("p_dead", "t_dead"))
    findings = {f.code: f for f in structural_findings(net, Marking({nodes["source"]: 1}), Marking({nodes["sink"]: 1}))}
    assert "cannot_reach_sink" in findings
    assert {"p_dead", "t_dead"} <= set(findings["cannot_reach_sink"].element_ids)


def test_structurally_valid_is_not_the_same_as_sound():
    # AND-split followed by an XOR-join: a valid workflow net that leaves a token behind.
    net, n = _net(("source", "t_split"), ("t_split", "p1"), ("t_split", "p2"),
                  ("p1", "t_a"), ("p2", "t_b"), ("t_a", "sink"), ("t_b", "sink"))
    im, fm = Marking({n["source"]: 1}), Marking({n["sink"]: 1})
    assert structural_findings(net, im, fm) == []
    verdict, detail = check_soundness(net, im, fm, max_states=1000, timeout_s=5)
    assert verdict == "failed" and "completion" in detail


def test_soundness_budget_exhaustion_is_not_checked():
    net, im, fm, _ = convert(AND)
    assert check_soundness(net, im, fm, max_states=1, timeout_s=5)[0] == "not_checked"
