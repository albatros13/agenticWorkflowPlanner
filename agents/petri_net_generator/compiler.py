"""Deterministic compiler: process-algebra IR -> ordinary workflow Petri net (PM4Py objects).

Every construct is compiled *between* a given input place and output place:

* activity  -> one visible transition ``in -> t -> out`` (transition name = activity id);
* sequence  -> steps chained through fresh intermediate places;
* xor       -> every branch compiled between the *same* in/out places, so the first transition
               of a branch consumes the single token and disables the others; a skip branch is a
               silent transition;
* parallel  -> a silent split puts a token in each branch, a silent join waits for all of them;
* loop      -> silent enter -> body -> (silent exit | redo -> back to body).

Silent (tau) transitions guard every loop/parallel boundary, so sharing an in/out place with an
enclosing xor never lets a loop's back-edge or a join leak into a sibling branch. Names are
counters, so the same IR always yields the same net (stable PNML IDs).
"""
from __future__ import annotations

from pm4py.objects.petri_net.obj import Marking, PetriNet
from pm4py.objects.petri_net.utils.petri_utils import add_arc_from_to

from .ir import Activity, Loop, Parallel, ProcessIR, Sequence, Xor

SOURCE = "source"
SINK = "sink"


class _Builder:
    def __init__(self, name: str):
        self.net = PetriNet(name)
        self._places = 0
        self._taus = 0

    def place(self, name: str | None = None) -> PetriNet.Place:
        if name is None:
            self._places += 1
            name = f"p{self._places}"
        p = PetriNet.Place(name)
        self.net.places.add(p)
        return p

    def transition(self, name: str, label: str | None) -> PetriNet.Transition:
        t = PetriNet.Transition(name, label)
        self.net.transitions.add(t)
        return t

    def tau(self, role: str) -> PetriNet.Transition:
        self._taus += 1
        return self.transition(f"tau{self._taus}_{role}", None)

    def connect(self, src: PetriNet.Place, t: PetriNet.Transition, dst: PetriNet.Place) -> None:
        add_arc_from_to(src, t, self.net)
        add_arc_from_to(t, dst, self.net)

    def compile(self, expr, p_in: PetriNet.Place, p_out: PetriNet.Place) -> None:
        if isinstance(expr, Activity):
            self.connect(p_in, self.transition(expr.id, expr.label), p_out)
        elif isinstance(expr, Sequence):
            current = p_in
            for i, step in enumerate(expr.steps):
                nxt = p_out if i == len(expr.steps) - 1 else self.place()
                self.compile(step, current, nxt)
                current = nxt
        elif isinstance(expr, Xor):
            for branch in expr.branches:
                if branch.body is None:
                    self.connect(p_in, self.tau("skip"), p_out)
                else:
                    self.compile(branch.body, p_in, p_out)
        elif isinstance(expr, Parallel):
            split, join = self.tau("and_split"), self.tau("and_join")
            add_arc_from_to(p_in, split, self.net)
            add_arc_from_to(join, p_out, self.net)
            for branch in expr.branches:
                b_in, b_out = self.place(), self.place()
                add_arc_from_to(split, b_in, self.net)
                self.compile(branch, b_in, b_out)
                add_arc_from_to(b_out, join, self.net)
        elif isinstance(expr, Loop):
            body_in, body_out = self.place(), self.place()
            self.connect(p_in, self.tau("loop_enter"), body_in)
            self.compile(expr.body, body_in, body_out)
            self.connect(body_out, self.tau("loop_exit"), p_out)
            if expr.redo is None:
                self.connect(body_out, self.tau("loop_redo"), body_in)
            else:
                self.compile(expr.redo, body_out, body_in)
        else:  # pragma: no cover - the discriminated union makes this unreachable
            raise TypeError(f"Unsupported IR expression: {type(expr).__name__}")


def compile_ir(ir: ProcessIR) -> tuple[PetriNet, Marking, Marking]:
    """Compile a validated IR into an accepting Petri net ``(net, initial, final)``."""
    b = _Builder(ir.process_name or "process")
    source, sink = b.place(SOURCE), b.place(SINK)
    b.compile(ir.root, source, sink)
    return b.net, Marking({source: 1}), Marking({sink: 1})
