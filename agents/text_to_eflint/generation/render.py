"""Render an :class:`EflintSpec` into ``.eflint`` source, laid out like the reference framework.

Module files (each ``#require``-s the previous one, as upstream):

* ``Ontology.eflint`` — atomic types (``Identified by String|Int``);
* ``Norms.eflint`` — legal-condition / state facts, duties, acts;
* ``Init.eflint`` — domain instances and initial state;
* ``Scenario-<n>.eflint`` — one per scenario, ``?!Holds(..)`` for expected-false queries.

Plus ``spec.eflint``: Ontology + Norms + Init in one self-contained file. Expressions are
re-printed from their parse tree, so output is canonical; anything that does not parse is
emitted verbatim (the checker has already reported it).
"""
from __future__ import annotations

from .spec import ActDecl, DutyDecl, EflintSpec, FactDecl, Scenario, Statement
from .syntax import EflintSyntaxError, conjuncts, parse_expr, parse_term, to_text

INDENT = "    "


def _canon(text: str | None, *, term: bool = False) -> str:
    src = (text or "").strip().rstrip(".")
    try:
        return to_text(parse_term(src) if term else parse_expr(src))
    except EflintSyntaxError:
        return src


def _condition(keyword: str, text: str) -> list[str]:
    """``Holds when a && b`` -> one conjunct per line, as in the reference spec."""
    try:
        parts = [to_text(c, 2) for c in conjuncts(parse_expr(text.strip().rstrip(".")))]
    except EflintSyntaxError:
        parts = [text.strip().rstrip(".")]
    lines = [f"{INDENT}{keyword} {parts[0]}"]
    lines += [f"{INDENT * 2}&& {p}" for p in parts[1:]]
    return lines


def _comment(*parts: str) -> list[str]:
    text = " - ".join(p.strip() for p in parts if p and p.strip())
    return [f"// {line}".rstrip() for line in text.splitlines()] if text else []


def _end(lines: list[str]) -> list[str]:
    lines[-1] += "."
    return lines


def render_fact(f: FactDecl) -> list[str]:
    lines = _comment(f.source, f.explanation)
    lines.append(f"Fact {f.name} Identified by {' * '.join(f.identified_by or ['String'])}")
    if f.holds_when:
        lines += _condition("Holds when", f.holds_when)
    return _end(lines)


def render_duty(d: DutyDecl) -> list[str]:
    lines = _comment(d.source, d.explanation)
    lines += [f"Duty {d.name}", f"{INDENT}Holder {d.holder}", f"{INDENT}Claimant {d.claimant}"]
    if d.related_to:
        lines.append(f"{INDENT}Related to {', '.join(d.related_to)}")
    if d.violated_when:
        lines += _condition("Violated when", d.violated_when)
    return _end(lines)


def render_act(a: ActDecl) -> list[str]:
    lines = _comment(a.source, a.explanation)
    lines += [f"Act {a.name}", f"{INDENT}Actor {a.actor}"]
    if a.recipient:
        lines.append(f"{INDENT}Recipient {a.recipient}")
    if a.related_to:
        lines.append(f"{INDENT}Related to {', '.join(a.related_to)}")
    if a.holds_when:
        lines += _condition("Holds when", a.holds_when)
    lines += [f"{INDENT}Creates {_canon(t, term=True)}" for t in a.creates]
    lines += [f"{INDENT}Terminates {_canon(t, term=True)}" for t in a.terminates]
    return _end(lines)


def _blocks(blocks: list[list[str]]) -> str:
    return "\n\n".join("\n".join(b) for b in blocks if b)


def _statements(stmts: list[Statement]) -> list[str]:
    out = []
    for st in stmts:
        out += _comment(st.comment)
        out.append(f"+{_canon(st.term, term=True)}.")
    return out


def render_ontology(spec: EflintSpec) -> str:
    return _blocks([["// Ontology"], *(render_fact(f) for f in spec.facts if f.atomic)]) + "\n"


def render_norms(spec: EflintSpec, *, require: str | None = "Ontology.eflint") -> str:
    blocks = [[f'#require "{require}".'] if require else [], ["// Facts"]]
    blocks += [render_fact(f) for f in spec.facts if not f.atomic]
    if spec.duties:
        blocks += [["// Duties"], *(render_duty(d) for d in spec.duties)]
    if spec.acts:
        blocks += [["// Acts"], *(render_act(a) for a in spec.acts)]
    return _blocks(blocks) + "\n"


def render_init(spec: EflintSpec, *, require: str | None = "Norms.eflint") -> str:
    blocks = [[f'#require "{require}".'] if require else []]
    if spec.instances:
        blocks += [["// Domain Instances"], _statements(spec.instances)]
    if spec.initial_state:
        blocks += [["// Initial State"], _statements(spec.initial_state)]
    return _blocks(blocks) + "\n"


def render_scenario(sc: Scenario, n: int, *, require: str = "Init.eflint") -> str:
    lines = [f"// Scenario {n} - {sc.name}"] + _comment(sc.description) + ["", f'#require "{require}".', ""]
    for st in sc.steps:
        term = _canon(st.term, term=True)
        lines += _comment(st.comment)
        if st.kind == "assert":
            lines.append(f"+{term}.")
        elif st.kind == "retract":
            lines.append(f"-{term}.")
        elif st.kind == "perform":
            # eFLINT performs a disabled act anyway (and reports it), so expected-disabled acts
            # are rendered as an assertion that they are not enabled instead.
            lines.append(f"{term}." if st.expect else f"?!Enabled({term}).")
        else:
            wrap = {"holds": "Holds", "enabled": "Enabled", "violated": "Violated"}[st.query]
            lines.append(f"?{'' if st.expect else '!'}{wrap}({term}).")
    return "\n".join(lines) + "\n"


def render_single(spec: EflintSpec) -> str:
    header = [f"// {spec.title}"] if spec.title else []
    parts = ["\n".join(header), render_ontology(spec), render_norms(spec, require=None), render_init(spec, require=None)]
    return "\n".join(p.strip("\n") + "\n" for p in parts if p.strip())


def render_files(spec: EflintSpec) -> dict[str, str]:
    files = {
        "Ontology.eflint": render_ontology(spec),
        "Norms.eflint": render_norms(spec),
        "Init.eflint": render_init(spec),
    }
    for n, sc in enumerate(spec.scenarios, 1):
        files[f"Scenario-{n}.eflint"] = render_scenario(sc, n)
    files["spec.eflint"] = render_single(spec)
    return files
