"""Hand-built IR fixtures for deterministic BPMN/DMN compiler tests.

Not collected by pytest (leading underscore); imported by the compiler tests to
exercise the guideline's structural categories: sequential, XOR-with-threshold,
and AND/concurrent.
"""
from __future__ import annotations

from agents.text_to_bpmn.pipeline.ir.enums import ElementType, GatewayKind
from agents.text_to_bpmn.pipeline.ir.models import Element, Process, Relation, Threshold


def sequential() -> Process:
    return Process(
        id="seq", name="Sequential intake", actors=["Nurse", "Physician"],
        evidence_ids=["E1", "E2", "E3"],
        elements=[
            Element(id="S", type=ElementType.START, name="Start"),
            Element(id="A1", type=ElementType.ACTIVITY, actor="Nurse",
                    name="Record medical history", evidence_refs=["E1"]),
            Element(id="A2", type=ElementType.ACTIVITY, actor="Physician",
                    name="Perform physical examination", evidence_refs=["E2"]),
            Element(id="A3", type=ElementType.ACTIVITY, actor="Nurse",
                    name="Schedule follow-up", evidence_refs=["E3"]),
            Element(id="E", type=ElementType.END, name="End"),
        ],
        relations=[
            Relation(id="f1", type="sequence", source="S", target="A1"),
            Relation(id="f2", type="sequence", source="A1", target="A2"),
            Relation(id="f3", type="sequence", source="A2", target="A3"),
            Relation(id="f4", type="sequence", source="A3", target="E"),
        ],
    )


def xor_threshold() -> Process:
    return Process(
        id="xor", name="BPH follow-up", actors=["Family physician"],
        evidence_ids=["E1", "E2", "E3"],
        elements=[
            Element(id="S", type=ElementType.START, name="At follow-up"),
            Element(id="A1", type=ElementType.ACTIVITY, actor="Family physician",
                    name="Assess IPSS", evidence_refs=["E1"]),
            Element(id="D1", type=ElementType.DECISION, name="IPSS improved by >= 25%?",
                    gateway_kind=GatewayKind.XOR, evidence_refs=["E2", "E3"],
                    thresholds=[Threshold(measure="IPSS improvement", operator=">=",
                                          value=25, unit="%")]),
            Element(id="A2", type=ElementType.ACTIVITY, actor="Family physician",
                    name="Continue treatment", evidence_refs=["E2"]),
            Element(id="A3", type=ElementType.ACTIVITY, actor="Family physician",
                    name="Refer back to urologist", evidence_refs=["E3"]),
            Element(id="E", type=ElementType.END, name="End"),
        ],
        relations=[
            Relation(id="f1", type="sequence", source="S", target="A1"),
            Relation(id="f2", type="sequence", source="A1", target="D1"),
            Relation(id="f3", type="conditional", source="D1", target="A2",
                     condition="IPSS improvement of at least 25%"),
            Relation(id="f4", type="conditional", source="D1", target="A3",
                     condition="IPSS improvement of less than 25%"),
            Relation(id="f5", type="sequence", source="A2", target="E"),
            Relation(id="f6", type="sequence", source="A3", target="E"),
        ],
    )


def and_parallel() -> Process:
    return Process(
        id="par", name="Staging", actors=["Radiology"],
        evidence_ids=["E1", "E2"],
        elements=[
            Element(id="S", type=ElementType.START, name="Positive biopsy"),
            Element(id="G1", type=ElementType.GATEWAY, name="Split", gateway_kind=GatewayKind.AND),
            Element(id="A1", type=ElementType.ACTIVITY, actor="Radiology",
                    name="Chest X-ray", evidence_refs=["E1"]),
            Element(id="A2", type=ElementType.ACTIVITY, actor="Radiology",
                    name="Bone scan", evidence_refs=["E2"]),
            Element(id="G2", type=ElementType.GATEWAY, name="Join", gateway_kind=GatewayKind.AND),
            Element(id="E", type=ElementType.END, name="Staging complete"),
        ],
        relations=[
            Relation(id="f1", type="sequence", source="S", target="G1"),
            Relation(id="f2", type="parallel", source="G1", target="A1"),
            Relation(id="f3", type="parallel", source="G1", target="A2"),
            Relation(id="f4", type="sequence", source="A1", target="G2"),
            Relation(id="f5", type="sequence", source="A2", target="G2"),
            Relation(id="f6", type="sequence", source="G2", target="E"),
        ],
    )
