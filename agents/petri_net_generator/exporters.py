"""Serializers for accepting Petri nets: PNML (primary) and DOT (optional preview).

PNML is written here rather than with ``pm4py.write_pnml`` because PM4Py's exporter derives arc
IDs from Python object ids and emits elements in set order, so the same net would serialize
differently on every run. This exporter emits the same PT-net core model (``pnmlcoremodel``),
sorted and with stable IDs, plus PM4Py's ``finalmarkings`` extension and its ProM-style marker
for silent transitions, so PM4Py reads the output back losslessly (see the round-trip tests).
"""
from __future__ import annotations

from typing import Protocol

from lxml import etree
from pm4py.objects.petri_net.obj import Marking, PetriNet

PNML_GRAMMAR = "http://www.pnml.org/version-2009/grammar/pnmlcoremodel"


class NetExporter(Protocol):
    def __call__(self, net: PetriNet, im: Marking, fm: Marking) -> str: ...


def _text(parent: etree._Element, tag: str, value: str) -> etree._Element:
    el = etree.SubElement(parent, tag)
    etree.SubElement(el, "text").text = value
    return el


def to_pnml(net: PetriNet, im: Marking, fm: Marking) -> str:
    root = etree.Element("pnml")
    net_el = etree.SubElement(root, "net", id=net.name or "net", type=PNML_GRAMMAR)
    _text(net_el, "name", net.name or "net")
    page = etree.SubElement(net_el, "page", id="page0")

    initial = {p.name: n for p, n in im.items()}
    for place in sorted(net.places, key=lambda p: p.name):
        el = etree.SubElement(page, "place", id=place.name)
        _text(el, "name", place.name)
        if initial.get(place.name):
            _text(el, "initialMarking", str(initial[place.name]))

    for t in sorted(net.transitions, key=lambda t: t.name):
        el = etree.SubElement(page, "transition", id=t.name)
        _text(el, "name", t.label if t.label is not None else t.name)
        if t.label is None:
            etree.SubElement(el, "toolspecific", tool="ProM", version="6.4",
                             activity="$invisible$", localNodeID=t.name)

    arcs = sorted(net.arcs, key=lambda a: (a.source.name, a.target.name))
    for i, arc in enumerate(arcs, start=1):
        el = etree.SubElement(page, "arc", id=f"a{i}", source=arc.source.name, target=arc.target.name)
        if arc.weight != 1:
            _text(el, "inscription", str(arc.weight))

    finals = etree.SubElement(net_el, "finalmarkings")
    marking = etree.SubElement(finals, "marking")
    for place, n in sorted(fm.items(), key=lambda kv: kv[0].name):
        el = etree.SubElement(marking, "place", idref=place.name)
        etree.SubElement(el, "text").text = str(n)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True).decode("utf-8")


def to_dot(net: PetriNet, im: Marking, fm: Marking) -> str:
    """Graphviz preview: circles = places (token count shown), boxes = transitions (black = silent)."""
    initial = {p.name: n for p, n in im.items()}
    final = {p.name for p in fm}

    def q(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = [f"digraph {q(net.name or 'net')} {{", "  rankdir=LR;"]
    for p in sorted(net.places, key=lambda p: p.name):
        tokens = "●" * initial.get(p.name, 0)
        shape = "doublecircle" if p.name in final else "circle"
        lines.append(f"  {q('p:' + p.name)} [shape={shape} label={q(tokens)} xlabel={q(p.name)}];")
    for t in sorted(net.transitions, key=lambda t: t.name):
        if t.label is None:
            lines.append(f"  {q('t:' + t.name)} [shape=box style=filled fillcolor=black label=\"\" "
                         f"width=0.15 xlabel={q(t.name)}];")
        else:
            lines.append(f"  {q('t:' + t.name)} [shape=box label={q(t.label)}];")
    for a in sorted(net.arcs, key=lambda a: (a.source.name, a.target.name)):
        src = ("p:" if isinstance(a.source, PetriNet.Place) else "t:") + a.source.name
        dst = ("p:" if isinstance(a.target, PetriNet.Place) else "t:") + a.target.name
        lines.append(f"  {q(src)} -> {q(dst)};")
    lines.append("}")
    return "\n".join(lines) + "\n"
