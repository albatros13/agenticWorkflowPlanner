"""Guideline Step 3 validation rules for the IR."""
import pytest
from pydantic import ValidationError

from agents.text_to_bpmn.pipeline.ir.enums import ElementType, GatewayKind, RelationType, Uncertainty
from agents.text_to_bpmn.pipeline.ir.models import Element, Process, Relation, Threshold


def _activity(**kw):
    base = dict(id="A1", type=ElementType.ACTIVITY, name="Do thing", evidence_refs=["E1"])
    base.update(kw)
    return Element(**base)


def test_invalid_element_type_rejected():
    with pytest.raises(ValidationError):
        Element(id="X", type="Frobnicate", name="bad")  # not in enum


def test_meaningful_element_missing_evidence_rejected():
    with pytest.raises(ValidationError):
        Element(id="A1", type=ElementType.ACTIVITY, name="Prescribe finasteride")


def test_derived_element_needs_derived_from():
    with pytest.raises(ValidationError):
        Element(id="A1", type=ElementType.ACTIVITY, name="x", status=Uncertainty.DERIVED)
    # ...but is accepted when it records provenance:
    ok = Element(
        id="A1", type=ElementType.ACTIVITY, name="x",
        status=Uncertainty.DERIVED, derived_from=["E1"],
    )
    assert ok.status is Uncertainty.DERIVED


def test_malformed_gateway_rejected():
    with pytest.raises(ValidationError):
        Element(id="D1", type=ElementType.DECISION, name="IPSS improved?", evidence_refs=["E1"])
    good = Element(
        id="D1", type=ElementType.DECISION, name="IPSS improved?",
        gateway_kind=GatewayKind.XOR, evidence_refs=["E1"],
    )
    assert good.gateway_kind is GatewayKind.XOR


def test_non_gateway_with_gateway_kind_rejected():
    with pytest.raises(ValidationError):
        Element(id="A1", type=ElementType.ACTIVITY, name="x",
                evidence_refs=["E1"], gateway_kind=GatewayKind.AND)


def test_conditional_relation_requires_condition():
    with pytest.raises(ValidationError):
        Relation(id="r1", type=RelationType.CONDITIONAL, source="D1", target="A2")


def test_relation_endpoints_must_exist():
    with pytest.raises(ValidationError):
        Process(
            id="p", name="P",
            elements=[_activity(id="A1")],
            relations=[Relation(id="r", type=RelationType.SEQUENCE, source="A1", target="MISSING")],
        )


def test_uncertainty_states_preserved_through_roundtrip():
    el = Element(
        id="D1", type=ElementType.DECISION, name="Consider surveillance?",
        gateway_kind=GatewayKind.XOR, status=Uncertainty.AMBIGUOUS,
        evidence_refs=["E9"], thresholds=[Threshold(measure="PSA", operator=">", value=10)],
    )
    proc = Process(id="p", name="P", elements=[el], evidence_ids=["E9"])
    dumped = proc.model_dump_json()
    restored = Process.model_validate_json(dumped)
    r_el = restored.element("D1")
    assert r_el.status is Uncertainty.AMBIGUOUS
    assert r_el.gateway_kind is GatewayKind.XOR
    assert r_el.thresholds[0].operator == ">" and r_el.thresholds[0].value == 10
    # Round-trip preserves semantics exactly.
    assert restored.model_dump() == proc.model_dump()


def test_duplicate_element_ids_rejected():
    with pytest.raises(ValidationError):
        Process(id="p", name="P", elements=[_activity(id="A1"), _activity(id="A1")])
