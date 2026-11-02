"""Convert parsed eFLINT programs into :class:`EflintSpec` (the inverse of :mod:`.render`).

Used to load the vendored reference specification as a few-shot example and to accept an
existing ``.eflint`` specification as the base for an extension (``base_spec``).
"""
from __future__ import annotations

from .simulator import EvalError, Simulator, build_types
from .spec import ActDecl, DutyDecl, EflintSpec, FactDecl, Scenario, ScenarioStep, Statement
from .syntax import App, Decl, EflintSyntaxError, Not, Program, Wrap, to_text


def _opt(e) -> str | None:
    return to_text(e) if e is not None else None


def decl_to_spec(d: Decl, doc: str = ""):
    if d.kind == "Fact":
        return FactDecl(name=d.name, identified_by=d.identified_by, holds_when=_opt(d.holds_when), source=doc)
    if d.kind == "Duty":
        return DutyDecl(name=d.name, holder=d.holder, claimant=d.claimant, related_to=d.related_to,
                        violated_when=_opt(d.violated_when), source=doc)
    if d.conditioned_by is not None:
        raise EflintSyntaxError(f"'Conditioned by' on {d.name} is not representable in the spec format")
    return ActDecl(name=d.name, actor=d.actor, recipient=d.recipient, related_to=d.related_to,
                   holds_when=_opt(d.holds_when), creates=[to_text(t) for t in d.creates],
                   terminates=[to_text(t) for t in d.terminates], source=doc)


def program_to_spec(program: Program, *, title: str = "") -> EflintSpec:
    """Declarations + ``+fact`` statements; performs and queries are ignored (see scenarios)."""
    spec = EflintSpec(title=title)
    types = build_types(program.decls)
    for st in program.statements:
        if st.kind == "decl":
            item = decl_to_spec(st.decl, st.doc)
            {"Fact": spec.facts, "Duty": spec.duties, "Act": spec.acts}[st.decl.kind].append(item)
        elif st.kind == "assert":
            info = types.get(st.term.name)
            target = spec.instances if info is not None and info.base is not None else spec.initial_state
            target.append(Statement(term=to_text(st.term), comment=st.doc))
    return spec


def scenario_from_program(
    program: Program, source: str, *, name: str, description: str = "", simulate: bool = False
) -> Scenario:
    """Steps from the statements of file ``source``.

    By default expectations are literal eFLINT semantics: ``?e.`` expects ``e`` to hold, ``?!e.``
    expects it not to, and a performed act is expected to be enabled. With ``simulate=True`` they
    are taken from a simulation instead — needed for the upstream reference scenarios, which
    write ``?Holds(x). // False`` for expected-false results (the golden tests check the
    simulator against those comments).
    """
    sim = Simulator.from_decls(program.decls)
    steps: list[ScenarioStep] = []
    for st in program.statements:
        own = st.source == source
        try:
            if st.kind in ("assert", "retract"):
                sim.postulate(st.term, value=st.kind == "assert")
                if own:
                    steps.append(ScenarioStep(kind=st.kind, term=to_text(st.term), comment=st.doc))
            elif st.kind == "perform":
                _inst, enabled = sim.perform(st.term)
                if own:
                    steps.append(ScenarioStep(kind="perform", term=to_text(st.term),
                                              expect=enabled if simulate else True, comment=st.doc))
            elif st.kind == "query" and own:
                negated = isinstance(st.expr, Not)
                e = st.expr.expr if negated else st.expr
                term = e.term if isinstance(e, Wrap) else e
                if not isinstance(term, App):
                    raise EflintSyntaxError(f"query at line {st.line} is not a Holds/Enabled/Violated term")
                kind = e.kind.lower() if isinstance(e, Wrap) else "holds"
                expect = sim.query(e) if simulate else not negated
                steps.append(ScenarioStep(kind="query", term=to_text(term), query=kind, expect=expect, comment=st.doc))
        except EvalError as exc:
            raise EflintSyntaxError(f"{source}:{st.line}: {exc}") from None
    return Scenario(name=name, description=description, steps=steps)
