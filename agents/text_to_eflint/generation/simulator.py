"""A small reference interpreter for the eFLINT subset in :mod:`.syntax`.

It exists so generated specifications can be *executed* (scenarios run, expectations checked)
without the Haskell eFLINT toolchain, which turns the LLM's output into something we can test
and feed back for repair. It is deliberately small and follows eFLINT's semantics for the
constructs the generator uses:

* a fact/duty instance holds if it was postulated (``+f(..)``) or created by an act, and not
  since terminated; a composite fact with ``Holds when`` additionally holds whenever its
  condition holds (derivation);
* an atomic (``String``/``Int``) fact with ``Holds when`` cannot be enumerated, so the condition
  acts as a guard on postulated instances only (this reproduces the reference spec, where
  ``primary-controller`` holds for the postulated company only);
* an act instance is *enabled* when its typed arguments hold and its ``Holds when`` /
  ``Conditioned by`` hold; performing a disabled act is reported but its effects still apply,
  as in eFLINT;
* a duty is *violated* when it holds and its ``Violated when`` holds;
* ``f()`` fills every argument from the same-named variable in scope; a variable ``controller2``
  or ``controller'`` has type ``controller``; ``Exists``/``Forall`` range over the instances that
  currently hold.

The official interpreter remains the authority — differences are possible outside this subset.
"""
from __future__ import annotations

import math
import operator
import re
from dataclasses import dataclass, field
from itertools import product
from typing import Union

from .spec import ATOMIC_BASES
from .syntax import App, BinOp, Cmp, Decl, Expr, Lit, Not, Quant, Var, Wrap

Prim = Union[str, int, bool]


# Exists/Forall enumerate the product of their variables' domains; refuse a quantifier whose
# product exceeds this many bindings instead of hanging the request.
MAX_QUANTIFIER_BINDINGS = 100_000
_CMP = {"==": operator.eq, "!=": operator.ne, "<": operator.lt, "<=": operator.le, ">": operator.gt,
        ">=": operator.ge}


class EvalError(ValueError):
    pass


@dataclass(frozen=True)
class Inst:
    type: str
    args: tuple  # primitives for atomic components, Inst for composite ones

    def __str__(self) -> str:
        def show(a):
            return str(a) if isinstance(a, Inst) else (f'"{a}"' if isinstance(a, str) else str(a))
        return f"{self.type}({', '.join(show(a) for a in self.args)})"


def type_of_var(name: str) -> str:
    """``controller2`` / ``controller'`` -> ``controller`` (eFLINT variable decoration)."""
    return re.sub(r"(?:\d+|'+)$", "", name)


@dataclass
class TypeInfo:
    decl: Decl
    params: list[tuple[str, str]]  # (variable name, type name)

    @property
    def kind(self) -> str:
        return self.decl.kind

    @property
    def base(self) -> str | None:
        """``String``/``Int`` for atomic facts, else None."""
        ib = self.decl.identified_by
        return ib[0] if self.kind == "Fact" and len(ib) == 1 and ib[0] in ATOMIC_BASES else None


def build_types(decls: list[Decl]) -> dict[str, TypeInfo]:
    types: dict[str, TypeInfo] = {}
    for d in decls:
        if d.kind == "Fact":
            if len(d.identified_by) == 1 and d.identified_by[0] in ATOMIC_BASES:
                params = [(d.name, d.identified_by[0])]
            else:
                params = [(v, type_of_var(v)) for v in d.identified_by]
        elif d.kind == "Act":
            names = [d.actor, *([d.recipient] if d.recipient else []), *d.related_to]
            params = [(v, type_of_var(v)) for v in names]
        else:
            params = [(v, type_of_var(v)) for v in [d.holder, d.claimant, *d.related_to]]
        types[d.name] = TypeInfo(d, params)
    return types


@dataclass
class Simulator:
    types: dict[str, TypeInfo]
    state: set = field(default_factory=set)
    _stack: set = field(default_factory=set)

    @classmethod
    def from_decls(cls, decls: list[Decl]) -> "Simulator":
        return cls(build_types(decls))

    def fork(self) -> "Simulator":
        return Simulator(self.types, set(self.state))

    # --- values ------------------------------------------------------------------------------
    def _prim(self, v) -> Prim:
        if isinstance(v, Inst):
            if self.types[v.type].base is None:
                raise EvalError(f"{v} is not a primitive value")
            return v.args[0]
        return v

    def _coerce(self, v, type_name: str):
        if type_name in ATOMIC_BASES:
            p = self._prim(v)
            want = int if type_name == "Int" else str
            if not isinstance(p, want) or isinstance(p, bool):
                raise EvalError(f"expected {type_name}, got {p!r}")
            return p
        info = self.types.get(type_name)
        if info is None:
            raise EvalError(f"unknown type {type_name!r}")
        if info.base is not None:
            return self._coerce(v, info.base)
        if isinstance(v, Inst) and v.type == type_name:
            return v
        raise EvalError(f"expected an instance of {type_name}, got {v}")

    def construct(self, app: App, env: dict) -> Inst:
        info = self.types.get(app.name)
        if info is None:
            raise EvalError(f"unknown type {app.name!r}")
        if not app.args and info.params:
            vals = []
            for var, _ in info.params:
                if var not in env:
                    raise EvalError(f"{app.name}() needs variable {var!r} in scope")
                vals.append(env[var])
        else:
            if len(app.args) != len(info.params):
                raise EvalError(f"{app.name} takes {len(info.params)} argument(s), got {len(app.args)}")
            vals = [self.eval(a, env) for a in app.args]
        return Inst(app.name, tuple(self._coerce(v, t) for v, (_, t) in zip(vals, info.params, strict=True)))

    def bind(self, inst: Inst) -> dict:
        return {var: val for (var, _), val in zip(self.types[inst.type].params, inst.args)}

    # --- truth -------------------------------------------------------------------------------
    def holds(self, inst: Inst) -> bool:
        info = self.types[inst.type]
        if info.kind == "Act":
            return self.enabled(inst)
        if inst in self._stack:  # cyclic derivation: not derivable along this path
            return False
        cond = info.decl.holds_when
        if cond is None or info.kind == "Duty":
            return inst in self.state
        self._stack.add(inst)
        try:
            if info.base is not None:  # guard on postulated instances (domain not enumerable)
                return inst in self.state and self.truth(self.eval(cond, self.bind(inst)))
            return inst in self.state or self.truth(self.eval(cond, self.bind(inst)))
        finally:
            self._stack.discard(inst)

    def enabled(self, inst: Inst) -> bool:
        info = self.types[inst.type]
        if info.kind != "Act":
            raise EvalError(f"{inst.type} is not an act")
        env = self.bind(inst)
        for (_var, t), val in zip(info.params, inst.args, strict=True):
            if t in self.types and not self.holds(val if isinstance(val, Inst) else Inst(t, (val,))):
                return False
        for cond in (info.decl.holds_when, info.decl.conditioned_by):
            if cond is not None and not self.truth(self.eval(cond, env)):
                return False
        return True

    def violated(self, inst: Inst) -> bool:
        info = self.types[inst.type]
        if info.kind != "Duty":
            raise EvalError(f"{inst.type} is not a duty")
        cond = info.decl.violated_when
        return self.holds(inst) and cond is not None and self.truth(self.eval(cond, self.bind(inst)))

    def truth(self, v) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, Inst):
            return self.holds(v)
        raise EvalError(f"{v!r} is not a boolean")

    def domain(self, type_name: str) -> list:
        info = self.types.get(type_name)
        if info is None or type_name in ATOMIC_BASES:
            raise EvalError(f"cannot quantify over {type_name!r}")
        insts = sorted((i for i in self.state if i.type == type_name and self.holds(i)), key=str)
        return [i.args[0] for i in insts] if info.base is not None else insts

    def eval(self, e: Expr, env: dict):
        if isinstance(e, Lit):
            return e.value
        if isinstance(e, Var):
            if e.name in env:
                return env[e.name]
            raise EvalError(f"unbound variable {e.name!r}")
        if isinstance(e, App):
            return self.construct(e, env)
        if isinstance(e, Wrap):
            inst = self.construct(e.term, env)
            if e.kind == "Holds":
                return self.holds(inst)
            return self.enabled(inst) if e.kind == "Enabled" else self.violated(inst)
        if isinstance(e, Not):
            return not self.truth(self.eval(e.expr, env))
        if isinstance(e, BinOp):
            left = self.truth(self.eval(e.left, env))
            if e.op == "&&":
                return left and self.truth(self.eval(e.right, env))
            return left or self.truth(self.eval(e.right, env))
        if isinstance(e, Cmp):
            a, b = self._prim(self.eval(e.left, env)), self._prim(self.eval(e.right, env))
            try:
                return _CMP[e.op](a, b)
            except TypeError as exc:
                raise EvalError(f"cannot compare {a!r} and {b!r}") from exc
        if isinstance(e, Quant):
            doms = [self.domain(type_of_var(v)) for v in e.vars]
            bindings = math.prod(len(d) for d in doms)
            if bindings > MAX_QUANTIFIER_BINDINGS:
                raise EvalError(f"{e.kind} over {', '.join(e.vars)} has {bindings} bindings "
                                f"(limit {MAX_QUANTIFIER_BINDINGS})")
            results = (self.truth(self.eval(e.body, {**env, **dict(zip(e.vars, combo, strict=True))}))
                       for combo in product(*doms))
            return any(results) if e.kind == "Exists" else all(results)
        raise EvalError(f"cannot evaluate {e!r}")

    # --- statements --------------------------------------------------------------------------
    def postulate(self, term: App, *, value: bool = True) -> Inst:
        inst = self.construct(term, {})
        if self.types[inst.type].kind == "Act":
            raise EvalError(f"cannot postulate act {inst.type}; perform it instead")
        (self.state.add if value else self.state.discard)(inst)
        return inst

    def perform(self, term: App) -> tuple[Inst, bool]:
        """Execute an act; returns ``(instance, was_enabled)``. Effects apply either way."""
        inst = self.construct(term, {})
        info = self.types[inst.type]
        if info.kind != "Act":
            raise EvalError(f"{inst.type} is not an act")
        was_enabled = self.enabled(inst)
        env = self.bind(inst)
        terminated = [self.construct(t, env) for t in info.decl.terminates]
        created = [self.construct(t, env) for t in info.decl.creates]
        self.state.difference_update(terminated)
        self.state.update(created)
        return inst, was_enabled

    def query(self, expr: Expr) -> bool:
        return self.truth(self.eval(expr, {}))
