"""Guideline Step 5: normalization annotates but never alters clinical meaning."""
from agents.text_to_bpmn.pipeline.ir.enums import ElementType, GatewayKind, Uncertainty
from agents.text_to_bpmn.pipeline.ir.models import Element, Process, Threshold
from agents.text_to_bpmn.pipeline.terminology.models import MatchType
from agents.text_to_bpmn.pipeline.terminology.resolver import TerminologyResolver


def resolver():
    return TerminologyResolver.from_config()


def test_known_term_resolves_with_ontology():
    m = resolver().resolve("PSA")
    assert m.ontology == "LOINC"
    assert m.concept_id == "2857-1"
    assert m.match_type is MatchType.EXACT
    # Source wording is preserved exactly.
    assert m.surface_form == "PSA"


def test_unknown_term_returns_none_match_not_error():
    m = resolver().resolve("quantum urology")
    assert m.match_type is MatchType.NONE
    assert m.normalized_label is None
    assert m.surface_form == "quantum urology"


def test_low_confidence_mapping_is_reviewable():
    m = resolver().resolve("alpha-blockers")  # synonym, confidence 0.8
    assert m.is_reviewable


def test_annotation_does_not_modify_element_name_or_threshold():
    el = Element(
        id="A1", type=ElementType.ACTIVITY,
        name="Measure PSA and prescribe finasteride",
        conditions=["PSA > 10"],
        thresholds=[Threshold(measure="PSA", operator=">", value=10)],
        evidence_refs=["E1"],
    )
    proc = Process(id="p", name="P", elements=[el], evidence_ids=["E1"])
    before = proc.model_dump()

    annotations = resolver().annotate_process(proc)

    # The process/element is untouched by annotation.
    assert proc.model_dump() == before
    assert proc.element("A1").name == "Measure PSA and prescribe finasteride"
    assert proc.element("A1").thresholds[0].value == 10

    # Both PSA and finasteride were mapped, keeping their source forms.
    mapped = {m.surface_form for a in annotations for m in a.mappings}
    assert {"PSA", "finasteride"} <= mapped
