"""PNML -> view model for the Petri net viewer demo (parsing, token game, DOT).

Standard libraries do the standard work:

* **PM4Py** parses PNML (``import_net_from_string``) and provides the firing semantics
  (``semantics.enabled_transitions`` / ``semantics.execute``) for the token game;
* **Graphviz** draws the net. This module only writes DOT in the usual Petri net notation
  (circles with tokens, boxes for transitions, black bars for silent ones). The browser renders it
  with Graphviz compiled to WebAssembly (``@viz-js/viz``), or the server does when the ``dot``
  binary is available.

PNML ``<graphics><position>`` coordinates (written by editors such as WoPeD or YAPNE) are honoured:
when every node has one, the drawing is pinned to them; otherwise Graphviz lays the net out.
PM4Py drops these coordinates on import, so they are read here with a hardened lxml parser,
which also rejects DTDs/entities before an uploaded document reaches PM4Py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from lxml import etree
from pm4py.objects.petri_net import semantics
from pm4py.objects.petri_net.importer.variants.pnml import import_net_from_string
from pm4py.objects.petri_net.obj import Marking, PetriNet

from ..config import Limits
from ..net_validation import check_soundness, structural_findings


class PnmlError(ValueError):
    """The document is not a PNML net this viewer can show."""


@dataclass
class ParsedNet:
    net: PetriNet
    initial: Marking
    final: Marking
    positions: dict[str, tuple[float, float]] = field(default_factory=dict)   # XML id -> (x, y)
    final_declared: bool = True   # False: no <finalmarkings> in the file, PM4Py inferred one

    def place(self, pid: str) -> PetriNet.Place:
        return next(p for p in self.net.places if p.name == pid)


_SAFE_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                               huge_tree=False, remove_comments=True)


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def parse_pnml(text: str | bytes, *, max_bytes: int | None = None) -> ParsedNet:
    data = text.encode("utf-8") if isinstance(text, str) else text
    limit = max_bytes or Limits.from_env().max_input_bytes
    if len(data) > limit:
        raise PnmlError(f"PNML is larger than {limit} bytes")
    try:
        root = etree.fromstring(data, _SAFE_PARSER)
    except etree.XMLSyntaxError as exc:
        raise PnmlError(f"not well-formed XML: {exc}") from exc
    if root.getroottree().docinfo.doctype:
        raise PnmlError("PNML with a DOCTYPE is not accepted")
    if _local(root.tag) != "pnml":
        raise PnmlError(f"root element is <{_local(root.tag)}>, expected <pnml>")

    positions: dict[str, tuple[float, float]] = {}
    final_declared = any(_local(el.tag) == "finalmarkings" for el in root.iter())
    for el in root.iter():
        if _local(el.tag) in ("place", "transition") and el.get("id"):
            for g in el:
                if _local(g.tag) != "graphics":
                    continue
                for pos in g:
                    if _local(pos.tag) == "position" and pos.get("x") is not None:
                        try:
                            positions[el.get("id")] = (float(pos.get("x")), float(pos.get("y", 0)))
                        except ValueError:
                            pass
    try:
        net, im, fm = import_net_from_string(etree.tostring(root))
    except Exception as exc:  # PM4Py raises assorted errors for unsupported content
        raise PnmlError(f"PM4Py could not import the net: {exc}") from exc
    if not net.places and not net.transitions:
        raise PnmlError("the PNML contains no places or transitions")
    return ParsedNet(net=net, initial=im, final=fm, positions=positions, final_declared=final_declared)


def marking_from(parsed: ParsedNet, marking: dict[str, int] | None) -> Marking:
    if marking is None:
        return Marking(parsed.initial)
    names = {p.name: p for p in parsed.net.places}
    m = Marking()
    for pid, n in marking.items():
        if pid not in names:
            raise PnmlError(f"unknown place {pid!r} in marking")
        if int(n) < 0:
            raise PnmlError(f"negative token count for place {pid!r}")
        if int(n):
            m[names[pid]] = int(n)
    return m


def fire(parsed: ParsedNet, marking: dict[str, int] | None, transition_id: str) -> dict[str, int]:
    m = marking_from(parsed, marking)
    t = next((t for t in parsed.net.transitions if t.name == transition_id), None)
    if t is None:
        raise PnmlError(f"unknown transition {transition_id!r}")
    if not semantics.is_enabled(t, parsed.net, m):
        raise PnmlError(f"transition {_label(t)!r} is not enabled in the current marking")
    return _mdict(semantics.execute(t, parsed.net, m))


def _mdict(m: Marking) -> dict[str, int]:
    return {p.name: n for p, n in sorted(m.items(), key=lambda kv: kv[0].name) if n}


def _label(t: PetriNet.Transition) -> str:
    return t.label if t.label is not None else t.name


def _place_label(p: PetriNet.Place) -> str:
    return p.properties.get("place_name_tag") or p.name


def build_view(parsed: ParsedNet, marking: dict[str, int] | None = None, *, rankdir: str = "LR",
               analyze: bool = False, name: str = "") -> dict:
    """Everything the viewer needs for one marking: nodes, enabled transitions, status and DOT."""
    net = parsed.net
    m = marking_from(parsed, marking)
    enabled = {t.name for t in semantics.enabled_transitions(net, m)}
    current, final = _mdict(m), _mdict(parsed.final)
    places = sorted(net.places, key=lambda p: p.name)
    transitions = sorted(net.transitions, key=lambda t: t.name)
    if final and current == final:
        status = "final"
    elif not enabled:
        status = "deadlock" if final else "dead"   # without a final marking we cannot call it a deadlock
    else:
        status = "running"

    view = {
        "name": name or net.name or "net",
        "places": [{"id": p.name, "label": _place_label(p), "tokens": current.get(p.name, 0),
                    "final": p.name in final, "svg_id": f"p{i}"} for i, p in enumerate(places)],
        "transitions": [{"id": t.name, "label": _label(t), "silent": t.label is None,
                         "enabled": t.name in enabled, "svg_id": f"t{i}"} for i, t in enumerate(transitions)],
        "marking": current,
        "initial_marking": _mdict(parsed.initial),
        "final_marking": final,
        # PM4Py infers a final marking (places without outgoing arcs) when the PNML declares none.
        "final_marking_source": "pnml" if parsed.final_declared else ("inferred" if final else "none"),
        "enabled": sorted(enabled),
        "status": status,
        "layout": "pnml" if _all_positioned(parsed) else "auto",
        "summary": {"places": len(net.places), "transitions": len(net.transitions),
                    "silent_transitions": sum(t.label is None for t in net.transitions),
                    "arcs": len(net.arcs)},
        "dot": to_dot(parsed, m, enabled, rankdir=rankdir),
    }
    if analyze:
        view["findings"] = [f.model_dump(mode="json") for f in structural_findings(net, parsed.initial, parsed.final)]
        if not final:
            view["soundness"] = {"verdict": "not_checked",
                                 "detail": "The PNML has no final marking, so soundness is undefined."}
        else:
            lim = Limits.from_env()
            verdict, detail = check_soundness(net, parsed.initial, parsed.final,
                                              max_states=lim.max_states, timeout_s=lim.soundness_timeout_s)
            if not parsed.final_declared:
                detail = ("Checked against the final marking inferred by PM4Py (the PNML declares none). "
                          + detail).strip()
            view["soundness"] = {"verdict": verdict, "detail": detail}
    return view


def _all_positioned(parsed: ParsedNet) -> bool:
    ids = [p.name for p in parsed.net.places] + [t.name for t in parsed.net.transitions]
    return bool(ids) and all(i in parsed.positions for i in ids)


def _q(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _tokens(n: int) -> str:
    if n == 0:
        return ""
    if n <= 4:
        return {1: "●", 2: "● ●", 3: "● ●\\n●", 4: "● ●\\n● ●"}[n]
    return str(n)


def to_dot(parsed: ParsedNet, marking: Marking, enabled: set[str], *, rankdir: str = "LR") -> str:
    net = parsed.net
    tokens = {p.name: n for p, n in marking.items()}
    final = {p.name for p in parsed.final}
    pinned = _all_positioned(parsed)
    lines = [f"digraph {_q(net.name or 'net')} {{"]
    if pinned:  # standard PNML layout: points, y axis pointing down
        lines.append("  graph [layout=neato, inputscale=72, overlap=true, splines=true, outputorder=edgesfirst];")
    else:
        lines.append(f"  graph [rankdir={'TB' if rankdir == 'TB' else 'LR'}, nodesep=0.35, ranksep=0.45];")
    lines.append('  node [fontname="Helvetica", fontsize=11]; edge [arrowsize=0.7, color="#475569"];')

    def pos(xml_id: str) -> str:
        if not pinned:
            return ""
        x, y = parsed.positions[xml_id]
        return f', pos="{x:g},{-y:g}!"'

    for i, p in enumerate(sorted(net.places, key=lambda p: p.name)):
        n = tokens.get(p.name, 0)
        lines.append(
            f"  {_q('p:' + p.name)} [id={_q(f'p{i}')}, shape=circle, fixedsize=true, width=0.5, "
            f"label={_q(_tokens(n))}, fontsize={11 if n > 4 else 13}, xlabel={_q(_place_label(p))}, "
            f"peripheries={2 if p.name in final else 1}, color=\"#1e293b\", "
            f"tooltip={_q(f'place {_place_label(p)}: {n} token(s)')}{pos(p.name)}];")
    for i, t in enumerate(sorted(net.transitions, key=lambda t: t.name)):
        on = t.name in enabled
        common = f"id={_q(f't{i}')}, tooltip={_q(('silent ' if t.label is None else '') + 'transition ' + _label(t) + (' (enabled, click to fire)' if on else ''))}"
        if t.label is None:
            lines.append(f"  {_q('t:' + t.name)} [{common}, shape=box, label=\"\", width=0.12, height=0.45, "
                         f"style=filled, fillcolor={_q('#15803d' if on else '#111827')}, "
                         f"color={_q('#15803d' if on else '#111827')}{pos(t.name)}];")
        else:
            lines.append(f"  {_q('t:' + t.name)} [{common}, shape=box, label={_q(_label(t))}, style=\"filled,rounded\", "
                         f"fillcolor={_q('#dcfce7' if on else '#ffffff')}, color={_q('#15803d' if on else '#1e293b')}, "
                         f"penwidth={2 if on else 1}, margin=\"0.12,0.06\"{pos(t.name)}];")
    for a in sorted(net.arcs, key=lambda a: (a.source.name, a.target.name)):
        src = ("p:" if isinstance(a.source, PetriNet.Place) else "t:") + a.source.name
        dst = ("p:" if isinstance(a.target, PetriNet.Place) else "t:") + a.target.name
        label = f" [label={_q(a.weight)}]" if a.weight != 1 else ""
        lines.append(f"  {_q(src)} -> {_q(dst)}{label};")
    lines.append("}")
    return "\n".join(lines) + "\n"
