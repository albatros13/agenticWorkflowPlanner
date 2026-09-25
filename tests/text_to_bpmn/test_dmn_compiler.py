"""Guideline Step 6: deterministic DMN decision-table generation."""
from lxml import etree

from agents.text_to_bpmn.pipeline.bpmn.compiler import BpmnCompiler
from agents.text_to_bpmn.pipeline.bpmn.namespaces import DMN
from agents.text_to_bpmn.pipeline.validation.schema import validate_dmn

from .._ir_fixtures import xor_threshold


def _texts(xml, local):
    doc = etree.fromstring(xml.encode())
    return [e.text for e in doc.iter(f"{{{DMN}}}{local}")]


def test_thresholded_decision_produces_valid_dmn():
    model = BpmnCompiler().compile(xor_threshold())
    assert model.dmn_xml is not None
    result = validate_dmn(model.dmn_xml)
    assert result.ok, result.errors


def test_decision_table_has_input_measure_and_feel_rules():
    dmn = BpmnCompiler().compile(xor_threshold()).dmn_xml
    doc = etree.fromstring(dmn.encode())

    # One decision table with the measure as its input.
    assert doc.find(f".//{{{DMN}}}decisionTable") is not None
    input_texts = _texts(dmn, "text")
    assert "IPSS improvement" in input_texts

    # FEEL unary tests derived from the branch conditions (>= 25 and < 25).
    joined = " ".join(t for t in input_texts if t)
    assert ">= 25" in joined
    assert "< 25" in joined

    # Output entries are quoted FEEL string literals naming the branch targets.
    assert any('"Continue treatment"' == t for t in input_texts)
    assert any('"Refer back to urologist"' == t for t in input_texts)


def test_business_rule_task_links_to_generated_decision():
    model = BpmnCompiler().compile(xor_threshold())
    brt = next(n for n in model.graph.nodes if n.kind == "businessRuleTask")
    decision_ids = {d.id for d in model.graph.decisions}
    assert brt.dmn_ref in decision_ids
    # ...and the link is serialized in the BPMN extensionElements.
    assert brt.dmn_ref in model.bpmn_xml
