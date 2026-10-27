"""Structural checks, a bounded soundness check, and trace enumeration for accepting Petri nets.

* :func:`structural_findings` — arcs, IDs, markings, arc weights, and the workflow-net shape
  (one source place, one sink place, every node on a source->sink path).
* :func:`check_soundness` — explores the reachability graph under a state/time budget and
  reports ``passed`` / ``failed`` / ``not_checked``. ``passed`` is only returned when the full
  state space was explored: option to complete, proper completion, no dead transitions, bounded.
* :func:`visible_traces` — the visible-label sequences that reach the final marking (bounded by
  length); used by tests to assert control-flow semantics rather than file shapes.
"""
from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass

from pm4py.objects.petri_net.obj import Marking, PetriNet

from .schemas import Finding, Severity

# A marking as a hashable, canonical value: sorted ((place_name, tokens), ...).
State = tuple[tuple[str, int], ...]


def _state(counts: Counter) -> State:
    return tuple(sorted((p, n) for p, n in counts.items() if n > 0))


@dataclass
class _Index:
    pre: dict[str, Counter]        # transition name -> {place name: weight}
    post: dict[str, Counter]
    labels: dict[str, str | None]  # transition name -> visible label (None = silent)

    @classmethod
    def of(cls, net: PetriNet) -> "_Index":
        pre = {t.name: Counter() for t in net.transitions}
        post = {t.name: Counter() for t in net.transitions}
        for arc in net.arcs:
            if isinstance(arc.source, PetriNet.Place):
                pre[arc.target.name][arc.source.name] += arc.weight
            else:
                post[arc.source.name][arc.target.name] += arc.weight
        return cls(pre, post, {t.name: t.label for t in net.transitions})

    def enabled(self, state: State) -> list[str]:
        m = dict(state)
        return sorted(t for t, need in self.pre.items() if all(m.get(p, 0) >= n for p, n in need.items()))

    def fire(self, state: State, t: str) -> State:
        m = Counter(dict(state))
        m.subtract(self.pre[t])
        m.update(self.post[t])
        return _state(m)


def marking_dict(marking: Marking) -> dict[str, int]:
    return {p.name: n for p, n in sorted(marking.items(), key=lambda kv: kv[0].name)}


# --- structure ---------------------------------------------------------------------------------


def structural_findings(net: PetriNet, im: Marking, fm: Marking) -> list[Finding]:
    f: list[Finding] = []

    def add(sev: Severity, code: str, msg: str, ids: list[str] | None = None) -> None:
        f.append(Finding(severity=sev, code=code, message=msg, element_ids=sorted(ids or [])))

    names = [p.name for p in net.places] + [t.name for t in net.transitions]
    dupes = [n for n, c in Counter(names).items() if c > 1]
    if dupes:
        add(Severity.error, "duplicate_id", "Place/transition IDs must be unique.", dupes)

    nodes = set(net.places) | set(net.transitions)
    dangling = [f"{a.source.name}->{a.target.name}" for a in net.arcs
                if a.source not in nodes or a.target not in nodes]
    if dangling:
        add(Severity.error, "dangling_arc", "Arcs reference nodes that are not in the net.", dangling)
    bad_kind = [f"{a.source.name}->{a.target.name}" for a in net.arcs
                if isinstance(a.source, PetriNet.Place) == isinstance(a.target, PetriNet.Place)]
    if bad_kind:
        add(Severity.error, "invalid_arc", "Arcs must connect a place and a transition.", bad_kind)
    weighted = [f"{a.source.name}->{a.target.name}" for a in net.arcs if a.weight != 1]
    if weighted:
        add(Severity.error, "weighted_arc", "Only ordinary (weight 1) arcs are allowed.", weighted)

    for label, marking in (("initial", im), ("final", fm)):
        stray = [p.name for p in marking if p not in net.places]
        if stray:
            add(Severity.error, f"invalid_{label}_marking",
                f"The {label} marking references places that are not in the net.", stray)
        if sum(marking.values()) != 1 or len(marking) != 1:
            add(Severity.error, f"{label}_marking_not_single_token",
                f"A workflow net's {label} marking must be exactly one token in one place.",
                [p.name for p in marking])

    sources = [p for p in net.places if not p.in_arcs]
    sinks = [p for p in net.places if not p.out_arcs]
    if len(sources) != 1:
        add(Severity.error, "source_place_count",
            f"Expected exactly one source place (no incoming arcs), found {len(sources)}.",
            [p.name for p in sources])
    if len(sinks) != 1:
        add(Severity.error, "sink_place_count",
            f"Expected exactly one sink place (no outgoing arcs), found {len(sinks)}.",
            [p.name for p in sinks])
    if len(sources) == 1 and set(im) != {sources[0]}:
        add(Severity.error, "initial_marking_not_on_source",
            "The initial marking must mark the source place.", [sources[0].name])
    if len(sinks) == 1 and set(fm) != {sinks[0]}:
        add(Severity.error, "final_marking_not_on_sink",
            "The final marking must mark the sink place.", [sinks[0].name])

    sourceless = [t.name for t in net.transitions if not t.in_arcs]
    if sourceless:
        add(Severity.error, "transition_without_input",
            "Transitions without input places are always enabled.", sourceless)

    if len(sources) == 1 and len(sinks) == 1 and not dangling:
        forward = _reach(sources[0], lambda n: [a.target for a in n.out_arcs])
        backward = _reach(sinks[0], lambda n: [a.source for a in n.in_arcs])
        unreachable = [n.name for n in nodes if n not in forward]
        dead_end = [n.name for n in nodes if n not in backward]
        if unreachable:
            add(Severity.error, "unreachable_from_source",
                "Nodes are not on any path from the source place.", unreachable)
        if dead_end:
            add(Severity.error, "cannot_reach_sink",
                "Nodes have no path to the sink place (dead ends).", dead_end)
    return f


def _reach(start, step) -> set:
    seen, todo = {start}, [start]
    while todo:
        for nxt in step(todo.pop()):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen


def source_and_sink(net: PetriNet) -> tuple[str | None, str | None]:
    sources = [p.name for p in net.places if not p.in_arcs]
    sinks = [p.name for p in net.places if not p.out_arcs]
    return (sources[0] if len(sources) == 1 else None, sinks[0] if len(sinks) == 1 else None)


# --- behaviour ---------------------------------------------------------------------------------


def check_soundness(
    net: PetriNet, im: Marking, fm: Marking, *, max_states: int, timeout_s: float
) -> tuple[str, str]:
    """Return ``(verdict, detail)`` with verdict in ``passed`` / ``failed`` / ``not_checked``."""
    idx = _Index.of(net)
    start, final = _state(Counter({p.name: n for p, n in im.items()})), _state(
        Counter({p.name: n for p, n in fm.items()}))
    final_m = dict(final)
    deadline = time.monotonic() + timeout_s

    parent: dict[State, State | None] = {start: None}
    succ: dict[State, set[State]] = {}
    fired: set[str] = set()
    queue = deque([start])
    while queue:
        if len(parent) > max_states or time.monotonic() > deadline:
            return "not_checked", (f"State-space budget exhausted ({len(parent)} states, "
                                   f"limit {max_states} / {timeout_s}s).")
        s = queue.popleft()
        m = dict(s)
        if s != final and all(m.get(p, 0) >= n for p, n in final_m.items()):
            return "failed", f"Improper completion: reachable marking {m} covers the final marking."
        succ[s] = set()
        for t in idx.enabled(s):
            fired.add(t)
            nxt = idx.fire(s, t)
            succ[s].add(nxt)
            if nxt in parent:
                continue
            # Unboundedness (Karp-Miller style): nxt strictly covers one of its ancestors.
            anc = s
            nm = dict(nxt)
            while anc is not None:
                am = dict(anc)
                if nxt != anc and all(nm.get(p, 0) >= am.get(p, 0) for p in set(am) | set(nm)):
                    return "failed", "Net is unbounded: a reachable marking strictly covers an earlier one."
                anc = parent[anc]
            parent[nxt] = s
            queue.append(nxt)

    can_finish = {final} if final in succ else set()
    changed = True
    while changed:
        changed = False
        for s, nexts in succ.items():
            if s not in can_finish and nexts & can_finish:
                can_finish.add(s)
                changed = True
    stuck = [s for s in succ if s not in can_finish]
    if stuck:
        return "failed", f"No option to complete from reachable marking {dict(stuck[0])} (deadlock or livelock)."
    dead = sorted(set(idx.pre) - fired)
    if dead:
        return "failed", f"Dead transitions (never enabled): {', '.join(dead)}."
    return "passed", f"Explored {len(succ)} reachable markings."


def visible_traces(net: PetriNet, im: Marking, fm: Marking, *, max_len: int = 10) -> set[tuple[str, ...]]:
    """All visible-label sequences of length <= ``max_len`` that end in the final marking."""
    idx = _Index.of(net)
    start = _state(Counter({p.name: n for p, n in im.items()}))
    final = _state(Counter({p.name: n for p, n in fm.items()}))
    out: set[tuple[str, ...]] = set()
    seen = {(start, ())}
    todo = [(start, ())]
    while todo:
        s, trace = todo.pop()
        if s == final:
            out.add(trace)
        for t in idx.enabled(s):
            label = idx.labels[t]
            nxt_trace = trace + (label,) if label is not None else trace
            if len(nxt_trace) > max_len:
                continue
            item = (idx.fire(s, t), nxt_trace)
            if item not in seen:
                seen.add(item)
                todo.append(item)
    return out
