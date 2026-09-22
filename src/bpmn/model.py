"""Control-flow graph sitting between the IR and BPMN XML.

The compiler lowers the :class:`~src.ir.models.Process` into this flat graph of
BPMN nodes and flows, applying the deterministic mapping rules (guideline
Step 6). A ``Decision`` that carries thresholds is expanded into a
``businessRuleTask`` (linked to a generated DMN decision) followed by a gateway —
the standard BPMN+DMN pattern.

Scope note (Phase 5): the compiler covers the control-flow subset (events,
activities, subprocesses, gateways and sequence/conditional/parallel flows).
DataObject/Message elements and data/message relations are recorded but not yet
rendered as artifacts; that arrives with later phases.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..ir.enums import ElementType, GatewayKind, RelationType
from ..ir.models import Process

# BPMN concrete node kinds we emit.
START = "startEvent"
END = "endEvent"
TASK = "task"
BUSINESS_RULE_TASK = "businessRuleTask"
SUBPROCESS = "subProcess"
INTERMEDIATE = "intermediateThrowEvent"
EXCLUSIVE = "exclusiveGateway"
INCLUSIVE = "inclusiveGateway"
PARALLEL = "parallelGateway"

_GATEWAY_BY_KIND = {
    GatewayKind.XOR: EXCLUSIVE,
    GatewayKind.OR: INCLUSIVE,
    GatewayKind.AND: PARALLEL,
}

GATEWAY_KINDS = frozenset({EXCLUSIVE, INCLUSIVE, PARALLEL})
# Nodes that must participate in the control flow (used by connectivity checks).
FLOW_NODE_KINDS = frozenset(
    {START, END, TASK, BUSINESS_RULE_TASK, SUBPROCESS, INTERMEDIATE, *GATEWAY_KINDS}
)

_ELEMENT_KIND = {
    ElementType.START: START,
    ElementType.END: END,
    ElementType.ACTIVITY: TASK,
    ElementType.SUBPROCESS: SUBPROCESS,
    ElementType.EVENT: INTERMEDIATE,
    ElementType.MESSAGE: INTERMEDIATE,
}

# Relation types rendered as sequence flows (Phase 5 control-flow subset).
_SEQUENCE_LIKE = frozenset(
    {RelationType.SEQUENCE, RelationType.CONDITIONAL, RelationType.PARALLEL, RelationType.DEPENDENCY}
)


@dataclass
class BNode:
    id: str
    kind: str
    name: str
    lane: str | None = None
    dmn_ref: str | None = None  # decision id for a businessRuleTask


@dataclass
class BFlow:
    id: str
    source: str
    target: str
    name: str = ""
    condition: str | None = None


@dataclass
class DecisionSpec:
    """A DMN decision to generate, derived from a thresholded IR Decision."""

    id: str
    name: str
    inputs: list[str]                      # distinct measures
    rules: list["DecisionRule"] = field(default_factory=list)


@dataclass
class DecisionRule:
    inputs: dict[str, str]                 # measure -> FEEL unary test ("-" if none)
    output: str                            # target branch label (FEEL string literal body)


@dataclass
class BpmnGraph:
    process_id: str
    name: str
    nodes: list[BNode] = field(default_factory=list)
    flows: list[BFlow] = field(default_factory=list)
    lanes: list[str] = field(default_factory=list)     # ordered actor lane names
    decisions: list[DecisionSpec] = field(default_factory=list)

    def node(self, node_id: str) -> BNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def flow_nodes(self) -> list[BNode]:
        return [n for n in self.nodes if n.kind in FLOW_NODE_KINDS]


def _feel_test(operator: str, value: float, value_high: float | None) -> str:
    """Render a threshold as a FEEL unary test for a DMN input entry."""
    v = _num(value)
    op = operator.strip().lower()
    if op == "between" and value_high is not None:
        return f"[{v}..{_num(value_high)}]"
    return {">": f"> {v}", ">=": f">= {v}", "<": f"< {v}", "<=": f"<= {v}", "==": f"{v}", "=": f"{v}"}.get(
        operator, f"{v}"
    )


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def build_graph(process: Process) -> BpmnGraph:
    """Lower an IR :class:`Process` into a :class:`BpmnGraph`."""
    graph = BpmnGraph(process_id=_ncname(process.id), name=process.name or process.id)

    # Lanes: keep declared actor order, then any actors seen on elements.
    lanes: list[str] = list(process.actors)
    for el in process.elements:
        if el.actor and el.actor not in lanes:
            lanes.append(el.actor)
    graph.lanes = lanes

    # First pass: decide which decisions expand (those with thresholds) and build nodes.
    expanded: dict[str, str] = {}  # decision id -> business-rule-task (eval) node id
    for el in process.elements:
        if el.type in (ElementType.DATA_OBJECT,):
            continue  # not part of the Phase 5 control-flow render
        if el.type in (ElementType.DECISION, ElementType.GATEWAY):
            kind = _GATEWAY_BY_KIND[el.gateway_kind]
            if el.type == ElementType.DECISION and el.thresholds:
                dec = _build_decision(el, process)
                graph.decisions.append(dec)
                eval_id = f"{_ncname(el.id)}_eval"
                expanded[el.id] = eval_id
                graph.nodes.append(
                    BNode(id=eval_id, kind=BUSINESS_RULE_TASK, name=f"Evaluate {el.name}",
                          lane=el.actor, dmn_ref=dec.id)
                )
            graph.nodes.append(BNode(id=_ncname(el.id), kind=kind, name=el.name, lane=el.actor))
            continue
        kind = _ELEMENT_KIND.get(el.type)
        if kind is None:
            continue
        graph.nodes.append(BNode(id=_ncname(el.id), kind=kind, name=el.name, lane=el.actor))

    # Second pass: flows, retargeting decision-incoming edges to the eval task.
    for rel in process.relations:
        if rel.type not in _SEQUENCE_LIKE and rel.type != RelationType.MESSAGE:
            continue  # skip pure data associations for now
        source = _ncname(rel.source)
        target = expanded.get(rel.target, _ncname(rel.target))
        if graph.node(source) is None or graph.node(target) is None:
            continue
        graph.flows.append(
            BFlow(
                id=_ncname(rel.id),
                source=source,
                target=target,
                condition=rel.condition if rel.type == RelationType.CONDITIONAL else None,
            )
        )

    # Internal eval -> gateway flows for expanded decisions.
    for dec_id, eval_id in expanded.items():
        graph.flows.append(BFlow(id=f"{eval_id}_flow", source=eval_id, target=_ncname(dec_id)))

    return graph


def _build_decision(element, process: Process) -> DecisionSpec:
    measures: list[str] = []
    for t in element.thresholds:
        if t.measure not in measures:
            measures.append(t.measure)

    rules: list[DecisionRule] = []
    for rel in process.outgoing(element.id):
        if rel.type != RelationType.CONDITIONAL:
            continue
        target = process.element(rel.target)
        label = target.name if target else (rel.condition or "")
        entries: dict[str, str] = {}
        for t in element.thresholds:
            if t.measure in (rel.condition or ""):
                # Whole condition mentions this measure; pick the sign from the text.
                entries[t.measure] = _test_from_condition(rel.condition, t)
        rules.append(DecisionRule(inputs=entries, output=label))

    # If no conditional relations produced rules, fall back to one rule per threshold.
    if not rules:
        for t in element.thresholds:
            rules.append(
                DecisionRule(
                    inputs={t.measure: _feel_test(t.operator, t.value, t.value_high)},
                    output=f"{t.measure} {t.operator} {_num(t.value)}",
                )
            )

    return DecisionSpec(
        id=f"dec_{_ncname(element.id)}",
        name=element.name,
        inputs=measures or [t.measure for t in element.thresholds],
        rules=rules,
    )


def _test_from_condition(condition: str, threshold) -> str:
    """Derive a FEEL unary test for ``threshold`` given the branch condition text.

    Recognises the comparison direction in the human-readable condition (``<``,
    ``>=``, "less than", "at least", ...); falls back to the threshold's own
    operator.
    """
    low = (condition or "").lower()
    v = _num(threshold.value)
    if threshold.operator == "between" and threshold.value_high is not None:
        return f"[{v}..{_num(threshold.value_high)}]"
    if any(s in low for s in ("<=", "no more than", "at most")):
        return f"<= {v}"
    if any(s in low for s in ("less than", "<", "below", "under")):
        return f"< {v}"
    if any(s in low for s in (">=", "at least", "no less than")):
        return f">= {v}"
    if any(s in low for s in ("greater than", ">", "above", "over", "more than")):
        return f"> {v}"
    return _feel_test(threshold.operator, threshold.value, threshold.value_high)


def _ncname(value: str) -> str:
    """Make a string safe to use as an XML id (NCName)."""
    out = []
    for ch in str(value):
        out.append(ch if ch.isalnum() or ch in "-_." else "_")
    name = "".join(out)
    if not name or not (name[0].isalpha() or name[0] == "_"):
        name = "_" + name
    return name
