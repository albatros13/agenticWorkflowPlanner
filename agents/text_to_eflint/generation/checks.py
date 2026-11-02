"""Validate an :class:`EflintSpec`: syntax, name resolution, typing, grounding, and scenario runs.

Findings are what the repair loop feeds back to the LLM, so every message names the element and
says what to change. Severity: ``error`` = the spec is wrong or will not run (repair is
triggered); ``warning`` = suspicious or weakly grounded; ``info`` = coverage hints.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .simulator import EvalError, Simulator, TypeInfo, build_types, type_of_var
from .spec import ATOMIC_BASES, EflintSpec, Finding, Severity
from .syntax import (
    KEYWORDS, VALID_NAME, App, Decl, EflintSyntaxError, Expr, Lit, Quant, Var, Wrap, parse_expr,
    parse_term, to_text, walk,
)


class StepResult(BaseModel):
    step: int
    kind: str
    term: str
    expected: bool | None = None
    actual: bool | None = None
    ok: bool = True
    detail: str = ""


class ScenarioResult(BaseModel):
    name: str
    passed: bool
    steps: list[StepResult] = Field(default_factory=list)


class Report(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    scenarios: list[ScenarioResult] = Field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.error]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.warning]

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> dict:
        return {
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "scenarios_passed": sum(s.passed for s in self.scenarios),
            "scenarios_total": len(self.scenarios),
        }


@dataclass
class _Ctx:
    findings: list[Finding] = field(default_factory=list)

    def add(self, sev: Severity, code: str, msg: str, element: str = "") -> None:
        self.findings.append(Finding(severity=sev, code=code, message=msg, element=element))

    def err(self, code, msg, el=""):
        self.add(Severity.error, code, msg, el)

    def warn(self, code, msg, el=""):
        self.add(Severity.warning, code, msg, el)

    def info(self, code, msg, el=""):
        self.add(Severity.info, code, msg, el)


# --- compiling the spec into parsed declarations -----------------------------------------------

def _parse(ctx: _Ctx, text: str | None, el: str, what: str, *, term: bool = False):
    if text is None or not str(text).strip():
        return None
    src = str(text).strip().rstrip(".")
    try:
        return parse_term(src) if term else parse_expr(src)
    except EflintSyntaxError as exc:
        ctx.err("syntax", f"{what} does not parse: {exc}", el)
        return None


def compile_decls(spec: EflintSpec, ctx: _Ctx | None = None) -> list[Decl]:
    ctx = ctx or _Ctx()
    decls: list[Decl] = []
    for f in spec.facts:
        decls.append(Decl("Fact", f.name, identified_by=list(f.identified_by) or ["String"],
                          holds_when=_parse(ctx, f.holds_when, f.name, "holds_when")))
    for d in spec.duties:
        decls.append(Decl("Duty", d.name, holder=d.holder, claimant=d.claimant, related_to=list(d.related_to),
                          violated_when=_parse(ctx, d.violated_when, d.name, "violated_when")))
    for a in spec.acts:
        decls.append(Decl(
            "Act", a.name, actor=a.actor, recipient=a.recipient or None, related_to=list(a.related_to),
            holds_when=_parse(ctx, a.holds_when, a.name, "holds_when"),
            creates=[t for t in (_parse(ctx, c, a.name, f"creates {c!r}", term=True) for c in a.creates) if t],
            terminates=[t for t in (_parse(ctx, c, a.name, f"terminates {c!r}", term=True) for c in a.terminates) if t],
        ))
    return decls


# --- static checks -----------------------------------------------------------------------------

def _check_names(ctx: _Ctx, decls: list[Decl]) -> None:
    seen: set[str] = set()
    for d in decls:
        if not VALID_NAME.match(d.name) or d.name in KEYWORDS:
            ctx.err("bad_name", f"{d.name!r} is not a valid eFLINT name (use lowercase kebab-case, e.g. 'give-consent')", d.name)
        if d.name in seen:
            ctx.err("duplicate", f"{d.name!r} is declared more than once (facts, duties and acts share one namespace)", d.name)
        seen.add(d.name)


def _type_ref(ctx: _Ctx, types: dict[str, TypeInfo], name: str | None, el: str, role: str) -> None:
    if not name:
        return
    t = type_of_var(name)
    if t not in types and t not in ATOMIC_BASES:
        ctx.err("unknown_type", f"{role} {name!r} refers to undeclared type {t!r}; declare it as a fact", el)
    elif t in types and types[t].kind == "Act":
        ctx.err("bad_type", f"{role} {name!r} refers to act {t!r}; parameters must be facts", el)


def _base(types: dict[str, TypeInfo], t: str) -> str | None:
    if t in ATOMIC_BASES:
        return t
    info = types.get(t)
    return info.base if info else None


def _compatible(types, arg_type: str, param_type: str) -> bool:
    if arg_type == param_type:
        return True
    a, p = _base(types, arg_type), _base(types, param_type)
    return a is not None and a == p  # atomic types over the same base convert implicitly


def _check_expr(ctx: _Ctx, types: dict[str, TypeInfo], e: Expr | None, scope: dict[str, str], el: str,
                what: str, *, ground: bool = False) -> None:
    """Resolve names, arities and argument types in ``e`` under variable ``scope`` (var -> type)."""
    if e is None:
        return

    def arg_type(a: Expr, local: dict[str, str]) -> str | None:
        if isinstance(a, Var):
            return local.get(a.name)
        if isinstance(a, App):
            return a.name
        if isinstance(a, Lit) and not isinstance(a.value, bool):
            return "Int" if isinstance(a.value, int) else "String"
        return None

    def visit(x: Expr, local: dict[str, str]) -> None:
        if isinstance(x, Var):
            if x.name not in local:
                hint = (" — literals must be quoted" if ground else
                        " — bind it as a parameter of the act/duty/fact or with 'Exists'")
                ctx.err("unbound_variable", f"{what}: variable {x.name!r} is not bound{hint}", el)
            return
        if isinstance(x, Quant):
            inner = dict(local)
            for v in x.vars:
                t = type_of_var(v)
                if t not in types or types[t].kind == "Act":
                    ctx.err("bad_quantifier", f"{what}: cannot quantify over {v!r} (no fact/duty type {t!r})", el)
                inner[v] = t
            visit(x.body, inner)
            return
        if isinstance(x, Wrap):
            want = {"Enabled": "Act", "Violated": "Duty"}.get(x.kind)
            info = types.get(x.term.name)
            if want and info and info.kind != want:
                ctx.err("bad_query", f"{what}: {x.kind}(...) needs a {want.lower()}, {x.term.name!r} is a {info.kind.lower()}", el)
            visit(x.term, local)
            return
        if isinstance(x, App):
            info = types.get(x.name)
            if info is None:
                ctx.err("unknown_type", f"{what}: {x.name!r} is not declared", el)
                for a in x.args:
                    visit(a, local)
                return
            if not x.args:
                if info.params and ground:
                    ctx.err("arity", f"{what}: {x.name}() needs {len(info.params)} explicit argument(s) here", el)
                for var, _t in info.params if not ground else ():
                    if var not in local:
                        ctx.err("unbound_variable",
                                f"{what}: {x.name}() fills argument {var!r} from scope, but {var!r} is not bound; "
                                f"pass the arguments explicitly", el)
                return
            if len(x.args) != len(info.params):
                expected = ", ".join(v for v, _ in info.params)
                ctx.err("arity", f"{what}: {x.name} takes {len(info.params)} argument(s) ({expected}), got {len(x.args)}", el)
            for a, (_v, p) in zip(x.args, info.params):
                at = arg_type(a, local)
                if at is None or at == p:
                    continue
                if not _compatible(types, at, p):
                    ctx.err("type_mismatch", f"{what}: argument {to_text(a)} of {x.name} has type {at!r}, expected {p!r}", el)
                elif info.base is None and at not in ATOMIC_BASES and p not in ATOMIC_BASES:
                    # e.g. a controller passed where a data-subject is expected. An atomic
                    # constructor such as controller(primary-controller) is an explicit conversion.
                    ctx.warn("implicit_conversion", f"{what}: argument {to_text(a)} of {x.name} has type {at!r} but "
                             f"the parameter is {p!r}; check the argument order, or convert explicitly with {p}({to_text(a)})", el)
            for a in x.args:
                visit(a, local)
            return
        for child in _children(x):
            visit(child, local)

    visit(e, scope)


def _children(x: Expr):
    for attr in ("expr", "left", "right", "body"):
        if hasattr(x, attr):
            yield getattr(x, attr)


def _static(ctx: _Ctx, spec: EflintSpec, decls: list[Decl], types: dict[str, TypeInfo]) -> None:
    _check_names(ctx, decls)
    for d in decls:
        info = types[d.name]
        scope = {v: t for v, t in info.params}
        if d.kind == "Fact":
            for comp in d.identified_by:
                if comp not in ATOMIC_BASES:
                    _type_ref(ctx, types, comp, d.name, "identified_by")
            if d.holds_when is not None and info.base is not None:
                ctx.warn("guard_on_atomic",
                         f"'Holds when' on {d.name} (identified by {info.base}) cannot derive instances; it only "
                         f"guards postulated ones — postulate the instances in the initial state instead", d.name)
            _check_expr(ctx, types, d.holds_when, scope, d.name, "holds_when")
        elif d.kind == "Act":
            for role, n in (("actor", d.actor), ("recipient", d.recipient), *(("related_to", r) for r in d.related_to)):
                _type_ref(ctx, types, n, d.name, role)
            if len({v for v, _ in info.params}) != len(info.params):
                ctx.err("duplicate_param", f"{d.name}: parameters must be distinct; use e.g. 'controller2' for a second controller", d.name)
            _check_expr(ctx, types, d.holds_when, scope, d.name, "holds_when")
            for kind, terms in (("creates", d.creates), ("terminates", d.terminates)):
                for t in terms:
                    _check_expr(ctx, types, t, scope, d.name, f"{kind} {to_text(t)}")
                    if t.name in types and types[t.name].kind == "Act":
                        ctx.err("bad_effect", f"{d.name} {kind} act {t.name!r}; only facts and duties can be created/terminated", d.name)
        else:
            for role, n in (("holder", d.holder), ("claimant", d.claimant), *(("related_to", r) for r in d.related_to)):
                _type_ref(ctx, types, n, d.name, role)
            if d.violated_when is None:
                ctx.warn("duty_never_violated", f"duty {d.name} has no violated_when, so it can never be violated", d.name)
            _check_expr(ctx, types, d.violated_when, scope, d.name, "violated_when")

    # Coverage: duties must arise somewhere; facts should be used.
    created = {t.name for d in decls if d.kind == "Act" for t in d.creates}
    terminated = {t.name for d in decls if d.kind == "Act" for t in d.terminates}
    postulated = set()
    for st in (*spec.instances, *spec.initial_state):
        try:
            postulated.add(parse_term(st.term.strip().rstrip(".")).name)
        except EflintSyntaxError:
            pass
    used: set[str] = set()
    for d in decls:
        for e in (d.holds_when, d.violated_when, d.conditioned_by, *d.creates, *d.terminates):
            if e is not None:
                used.update(n.name for n in walk(e) if isinstance(n, App))
                used.update(type_of_var(n.name) for n in walk(e) if isinstance(n, Var))
        used.update(type_of_var(p) for p, _ in types[d.name].params)
        used.update(c for c in d.identified_by)
    for d in decls:
        if d.kind == "Duty":
            if d.name not in created and d.name not in postulated:
                ctx.warn("duty_never_created", f"no act creates duty {d.name}; add it to the 'creates' of the act that triggers the obligation", d.name)
            if d.name not in terminated:
                ctx.info("duty_never_terminated", f"no act terminates duty {d.name} (e.g. the act that fulfils it)", d.name)
        elif d.kind == "Fact" and d.name not in used and d.name not in created and d.name not in postulated:
            ctx.info("unused_fact", f"fact {d.name} is declared but never used", d.name)


def _normalise(s: str) -> str:
    s = s.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


def _grounding(ctx: _Ctx, spec: EflintSpec, source_text: str | None) -> None:
    norm_text = _normalise(source_text) if source_text else None
    for d in (*spec.facts, *spec.duties, *spec.acts):
        is_atomic = getattr(d, "atomic", False)
        if not d.source.strip() and not is_atomic:
            ctx.warn("no_source", f"{d.name} has no source citation (article/paragraph, or 'domain model')", d.name)
        if norm_text is None or not d.source_quote.strip():
            continue
        if _normalise(d.source_quote).strip(" .\"'") not in norm_text:
            ctx.warn("ungrounded_quote",
                     f"source_quote of {d.name} is not a verbatim span of the input text: {d.source_quote[:80]!r}", d.name)


# --- statements and scenarios ------------------------------------------------------------------

def _ground_term(ctx: _Ctx, types, text: str, el: str, what: str, kinds: tuple[str, ...]) -> App | None:
    t = _parse(ctx, text, el, what, term=True)
    if t is None:
        return None
    before = len(ctx.findings)
    _check_expr(ctx, types, t, {}, el, what, ground=True)
    if len(ctx.findings) > before:
        return None
    if types[t.name].kind not in kinds:
        ctx.err("bad_statement", f"{what}: {t.name} is a {types[t.name].kind.lower()}, expected {' or '.join(k.lower() for k in kinds)}", el)
        return None
    return t


def _initial(ctx: _Ctx, spec: EflintSpec, sim: Simulator) -> list[App]:
    out = []
    for label, stmts in (("instances", spec.instances), ("initial_state", spec.initial_state)):
        for i, st in enumerate(stmts, 1):
            t = _ground_term(ctx, sim.types, st.term, f"{label}[{i}]", f"{label} {st.term!r}", ("Fact", "Duty"))
            if t is not None:
                out.append(t)
    return out


def _run_scenarios(ctx: _Ctx, spec: EflintSpec, sim: Simulator, init: list[App]) -> list[ScenarioResult]:
    base = sim.fork()
    for t in init:
        try:
            base.postulate(t)
        except EvalError as exc:
            ctx.err("eval", f"initial statement {to_text(t)}: {exc}", "initial_state")
    results = []
    for n, sc in enumerate(spec.scenarios, 1):
        s = base.fork()
        res = ScenarioResult(name=sc.name or f"Scenario {n}", passed=True)
        for i, step in enumerate(sc.steps, 1):
            el = f"scenario {n} ({sc.name}) step {i}"
            kinds = {"assert": ("Fact", "Duty"), "retract": ("Fact", "Duty"), "perform": ("Act",),
                     "query": {"holds": ("Fact", "Duty"), "enabled": ("Act",), "violated": ("Duty",)}[step.query]}[step.kind]
            sr = StepResult(step=i, kind=step.kind if step.kind != "query" else f"query:{step.query}", term=step.term)
            t = _ground_term(ctx, s.types, step.term, el, f"{step.kind} {step.term!r}", kinds)
            if t is None:
                sr.ok, sr.detail = False, "invalid term"
                res.steps.append(sr)
                res.passed = False
                continue
            sr.term = to_text(t)
            try:
                if step.kind in ("assert", "retract"):
                    s.postulate(t, value=step.kind == "assert")
                elif step.kind == "perform":
                    # An expected-disabled act is rendered as ?!Enabled(..) (not performed), so
                    # only check it here; expected-enabled acts are performed with their effects.
                    enabled = s.perform(t)[1] if step.expect else s.enabled(s.construct(t, {}))
                    sr.expected, sr.actual = step.expect, enabled
                    sr.ok = enabled == step.expect
                    if not sr.ok:
                        why = "is not enabled (its holds_when or argument facts do not hold)" if not enabled else "is enabled"
                        ctx.err("scenario_failed", f"performing {sr.term} {why}, but the scenario expects it to be "
                                f"{'enabled' if step.expect else 'disabled'}", el)
                else:
                    wrap = {"holds": "Holds", "enabled": "Enabled", "violated": "Violated"}[step.query]
                    actual = s.query(Wrap(wrap, t))
                    sr.expected, sr.actual, sr.ok = step.expect, actual, actual == step.expect
                    if not sr.ok:
                        ctx.err("scenario_failed", f"?{wrap}({sr.term}) is {actual}, the scenario expects {step.expect}", el)
            except EvalError as exc:
                sr.ok, sr.detail = False, str(exc)
                ctx.err("eval", f"{step.kind} {sr.term}: {exc}", el)
            if not sr.ok:
                res.passed = False
            res.steps.append(sr)
        results.append(res)
    return results


def check_spec(spec: EflintSpec, *, source_text: str | None = None, run_scenarios: bool = True) -> Report:
    """Run every check; scenarios execute only if the declarations themselves are error-free."""
    ctx = _Ctx()
    decls = compile_decls(spec, ctx)
    types = build_types(decls)
    _static(ctx, spec, decls, types)
    _grounding(ctx, spec, source_text)
    if not spec.scenarios:
        ctx.info("no_scenarios", "the specification has no scenarios, so its behaviour is untested")
    elif not any(not st.expect or st.query == "violated" for sc in spec.scenarios for st in sc.steps):
        ctx.info("no_negative_scenario", "no scenario shows a violation or a disabled act; add one per key norm")
    scenarios: list[ScenarioResult] = []
    if run_scenarios and not any(f.severity == Severity.error for f in ctx.findings):
        sim = Simulator(types)
        init = _initial(ctx, spec, sim)
        if not any(f.severity == Severity.error for f in ctx.findings):
            scenarios = _run_scenarios(ctx, spec, sim, init)
            performed = {st.term.split("(", 1)[0].strip() for sc in spec.scenarios for st in sc.steps if st.kind == "perform"}
            for a in spec.acts:
                if a.name not in performed:
                    ctx.info("act_untested", f"act {a.name} is never performed in any scenario", a.name)
    else:
        _initial(ctx, spec, Simulator(types))  # still report bad initial statements
    return Report(findings=ctx.findings, scenarios=scenarios)
