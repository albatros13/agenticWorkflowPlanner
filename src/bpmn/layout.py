"""Deterministic diagram-interchange (DI) layout.

Produces bounds for every node, plus optional pool/lane bands, and 2-point
waypoints for every flow. Nodes are placed left-to-right by their longest-path
rank; when the process has actors, each node is placed in its actor's horizontal
lane. Good enough for bpmn-js to render a clean, readable diagram.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import BpmnGraph, GATEWAY_KINDS, START, END, INTERMEDIATE

# Node sizes by kind.
_EVENT_KINDS = {START, END, INTERMEDIATE}
_TASK_W, _TASK_H = 100, 80
_GW = 50
_EV = 36

_COL_PITCH = 160
_LANE_H = 140
_POOL_X, _POOL_Y = 160, 80
_LANE_HEADER = 30
_LEFT_PAD = 40


@dataclass
class Rect:
    x: int
    y: int
    width: int
    height: int


@dataclass
class Geometry:
    nodes: dict[str, Rect] = field(default_factory=dict)
    edges: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    lanes: list[tuple[str, Rect]] = field(default_factory=list)  # (lane name, bounds)
    lane_members: dict[str, list[str]] = field(default_factory=dict)
    pool: Rect | None = None


def _size(kind: str) -> tuple[int, int]:
    if kind in _EVENT_KINDS:
        return _EV, _EV
    if kind in GATEWAY_KINDS:
        return _GW, _GW
    return _TASK_W, _TASK_H


def _ranks(graph: BpmnGraph) -> dict[str, int]:
    succ: dict[str, list[str]] = {n.id: [] for n in graph.nodes}
    indeg: dict[str, int] = {n.id: 0 for n in graph.nodes}
    for f in graph.flows:
        if f.source in succ and f.target in indeg:
            succ[f.source].append(f.target)
            indeg[f.target] += 1

    rank = {n.id: 0 for n in graph.nodes}
    # Longest-path relaxation over |nodes| iterations (handles cycles safely).
    starts = [nid for nid, dg in indeg.items() if dg == 0] or [n.id for n in graph.nodes[:1]]
    frontier = list(starts)
    for _ in range(len(graph.nodes) + 1):
        changed = False
        for src in list(frontier):
            for tgt in succ.get(src, []):
                if rank[tgt] < rank[src] + 1:
                    rank[tgt] = rank[src] + 1
                    changed = True
        frontier = list(rank)  # relax all again
        if not changed:
            break
    return rank


def layout(graph: BpmnGraph) -> Geometry:
    geo = Geometry()
    rank = _ranks(graph)
    max_rank = max(rank.values(), default=0)

    lanes = list(graph.lanes)
    use_lanes = bool(lanes)
    if use_lanes:
        # Any flow node without a recognised actor goes to a trailing lane.
        if any(n.lane not in lanes for n in graph.flow_nodes()):
            lanes = lanes + ["Unassigned"]

    lane_index = {name: i for i, name in enumerate(lanes)}

    pool_width = _LANE_HEADER + _LEFT_PAD + (max_rank + 1) * _COL_PITCH + 40
    lane_x = _POOL_X + _LANE_HEADER
    lane_width = pool_width - _LANE_HEADER

    # Track horizontal slot usage per (lane, rank) to avoid overlaps.
    used: dict[tuple[int, int], int] = {}

    for node in graph.nodes:
        w, h = _size(node.kind)
        r = rank[node.id]
        if use_lanes:
            li = lane_index.get(node.lane if node.lane in lane_index else "Unassigned", 0)
            lane_top = _POOL_Y + li * _LANE_H
        else:
            li = 0
            lane_top = _POOL_Y
        slot = used.get((li, r), 0)
        used[(li, r)] = slot + 1
        cx = _POOL_X + _LANE_HEADER + _LEFT_PAD + r * _COL_PITCH + (_TASK_W - w) // 2
        cy = lane_top + (_LANE_H - h) // 2 + slot * (h + 20)
        geo.nodes[node.id] = Rect(x=cx, y=cy, width=w, height=h)

    if use_lanes:
        for name in lanes:
            i = lane_index[name]
            geo.lanes.append((name, Rect(x=lane_x, y=_POOL_Y + i * _LANE_H, width=lane_width, height=_LANE_H)))
            geo.lane_members[name] = [
                n.id for n in graph.flow_nodes()
                if (n.lane if n.lane in lane_index else "Unassigned") == name
            ]
        geo.pool = Rect(x=_POOL_X, y=_POOL_Y, width=pool_width, height=len(lanes) * _LANE_H)

    # Edge waypoints: source right-mid -> target left-mid.
    for f in graph.flows:
        s, t = geo.nodes.get(f.source), geo.nodes.get(f.target)
        if not s or not t:
            continue
        start = (s.x + s.width, s.y + s.height // 2)
        end = (t.x, t.y + t.height // 2)
        geo.edges[f.id] = [start, end]

    return geo
