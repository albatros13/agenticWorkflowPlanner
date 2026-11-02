"""Small BPMN documents and process-algebra IRs used across the Petri net generator tests."""
from __future__ import annotations

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"


def bpmn(body: str, *, name: str = "Test process") -> str:
    """Wrap flow elements in a minimal single-process BPMN 2.0 document."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<definitions xmlns="{BPMN_NS}" id="defs" targetNamespace="https://example.org/test">'
        f'<process id="proc" name="{name}" isExecutable="false">{body}</process>'
        "</definitions>"
    )


def flow(fid: str, src: str, dst: str) -> str:
    return f'<sequenceFlow id="{fid}" sourceRef="{src}" targetRef="{dst}"/>'


MINIMAL = bpmn(
    '<startEvent id="S"/><task id="A" name="Do work"/><endEvent id="E"/>'
    + flow("f1", "S", "A") + flow("f2", "A", "E")
)

XOR = bpmn(
    '<startEvent id="S"/><task id="V" name="Validate"/>'
    '<exclusiveGateway id="G1"/><task id="A" name="Approve"/><task id="R" name="Reject"/>'
    '<exclusiveGateway id="G2"/><endEvent id="E"/>'
    + flow("f1", "S", "V") + flow("f2", "V", "G1") + flow("f3", "G1", "A") + flow("f4", "G1", "R")
    + flow("f5", "A", "G2") + flow("f6", "R", "G2") + flow("f7", "G2", "E")
)

AND = bpmn(
    '<startEvent id="S"/><parallelGateway id="G1"/><task id="X" name="X-ray"/>'
    '<task id="B" name="Bone scan"/><parallelGateway id="G2"/><task id="D" name="Discuss"/>'
    '<endEvent id="E"/>'
    + flow("f1", "S", "G1") + flow("f2", "G1", "X") + flow("f3", "G1", "B")
    + flow("f4", "X", "G2") + flow("f5", "B", "G2") + flow("f6", "G2", "D") + flow("f7", "D", "E")
)


def act(i: str, label: str | None = None) -> dict:
    return {"kind": "activity", "id": i, "label": label or i}


def ir(root: dict, **extra) -> dict:
    return {"process_name": "Test", "root": root, **extra}
