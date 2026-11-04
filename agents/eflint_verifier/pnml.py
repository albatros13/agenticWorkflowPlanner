"""Make PNML acceptable to the eFLINT verifier and list its node names.

The verifier's PNML importer requires every place/transition ``name/text`` to match
``[A-Za-z_][A-Za-z0-9_-]*``. Its analysis language is stricter (plain identifiers start with a
lowercase letter or ``_`` and end with a letter or ``_``). Our Petri net generator uses BPMN task
labels as transition names ("Submit request"), so names are normalised here to slugs valid in
*both* places (``submit-request``). Only names that need it are changed; XML IDs and arcs are
untouched, so the net's structure is unchanged. The rename map is reported back to the caller
because qualification rules and analyses must use the new names.

The importer also only accepts the *standard* P/T profile (namespace ``.../grammar/pnml``, net type
``.../grammar/ptnet``) or the YAPNE profile, and rejects unknown elements. PM4Py-style documents
(unnamespaced, ``pnmlcoremodel``, with a ``finalmarkings`` extension), which is what our generator
writes, are therefore rewritten into the standard profile; the final marking is dropped (the
verifier has no notion of it — properties express completion via ``Tokens``).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

PNML_NS = "http://www.pnml.org/version-2009/grammar/pnml"
PTNET_TYPE = "http://www.pnml.org/version-2009/grammar/ptnet"
_DROPPED = {"finalmarkings"}  # PM4Py extension, rejected by the importer

_ANALYSIS_NAME = re.compile(r"^[a-z_](?:[A-Za-z0-9_-]*[A-Za-z_])?$")


@dataclass
class PnmlNames:
    pnml: str
    transitions: list[str] = field(default_factory=list)
    places: list[str] = field(default_factory=list)
    renamed: dict[str, str] = field(default_factory=dict)   # original -> new
    changes: list[str] = field(default_factory=list)        # profile adjustments made


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "-", name.strip().lower()).strip("-")
    if not s:
        s = "node"
    if not re.match(r"[a-z_]", s):
        s = "t-" + s
    if not re.search(r"[A-Za-z_]$", s):
        s += "_"
    return s


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _name_text(node: ET.Element) -> ET.Element | None:
    for child in node:
        if _local(child.tag) == "name":
            for t in child:
                if _local(t.tag) == "text":
                    return t
    return None


def normalize_pnml(pnml: str) -> PnmlNames:
    """Return the PNML with verifier-safe names, plus the transition/place names it now uses.

    Raises ``ValueError`` for XML that does not parse.
    """
    try:
        root = ET.fromstring(pnml.encode("utf-8") if isinstance(pnml, str) else pnml)
    except ET.ParseError as exc:
        raise ValueError(f"PNML is not well-formed XML: {exc}") from exc

    out = PnmlNames(pnml=pnml)
    _to_standard_profile(root, out.changes)
    for kind, bucket in (("transition", out.transitions), ("place", out.places)):
        used: set[str] = set()
        nodes = [n for n in root.iter() if _local(n.tag) == kind]
        # Keep already-valid names first so a renamed node never steals an existing name.
        for node in nodes:
            text = _name_text(node)
            if text is not None and text.text and _ANALYSIS_NAME.match(text.text):
                used.add(text.text)
        for node in nodes:
            text = _name_text(node)
            original = (text.text if text is not None else None) or node.get("id") or kind
            if text is not None and text.text and _ANALYSIS_NAME.match(text.text):
                bucket.append(text.text)
                continue
            base, new, n = slug(original), slug(original), 2
            while new in used:
                new = f"{base.rstrip('_')}-{_alpha(n)}"
                n += 1
            used.add(new)
            if text is None:  # nameless node: the verifier requires one
                name_el = ET.SubElement(node, _sibling_tag(node, "name"))
                text = ET.SubElement(name_el, _sibling_tag(node, "text"))
            text.text = new
            out.renamed[original] = new
            bucket.append(new)

    if out.renamed or out.changes:
        if root.tag.startswith("{"):
            ET.register_namespace("", root.tag[1:].split("}", 1)[0])
        out.pnml = ET.tostring(root, encoding="unicode", xml_declaration=True)
    return out


def _to_standard_profile(root: ET.Element, changes: list[str]) -> None:
    for parent in list(root.iter()):
        for child in list(parent):
            if _local(child.tag) in _DROPPED:
                parent.remove(child)
                changes.append(f"removed unsupported <{_local(child.tag)}> element")
    if root.tag.startswith("{"):
        return  # namespaced: already the standard profile (or rejected by the service with a clear error)
    nets = [n for n in root if n.tag == "net"]
    if any(any(_local(c.tag) == "source" and "YAPNE" in "".join(c.itertext()) for c in n) for n in nets):
        return  # the service's YAPNE compatibility profile
    for el in root.iter():
        if not el.tag.startswith("{"):
            el.tag = f"{{{PNML_NS}}}{el.tag}"
    for n in nets:
        n.set("type", PTNET_TYPE)
    changes.append("converted to the standard PNML P/T-net profile")


def _alpha(n: int) -> str:
    """2 -> 'b', 3 -> 'c', ... ; analysis names may not end in a digit."""
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(ord("a") + r) + s
    return s


def _sibling_tag(node: ET.Element, local: str) -> str:
    return node.tag[: -len(_local(node.tag))] + local if "}" in node.tag else local


def pn_transitions(source: str) -> list[str]:
    """Transition names of a textual ``.pn`` net (``transition NAME: ...``)."""
    return re.findall(r"^\s*transition\s+([A-Za-z_][A-Za-z0-9_-]*)", source, flags=re.MULTILINE)
