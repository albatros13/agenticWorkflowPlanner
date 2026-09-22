"""Guideline Step 6/7: validators catch malformed and disconnected models."""
from src.bpmn.compiler import BpmnCompiler
from src.bpmn.namespaces import BPMN
from src.validation.schema import check_connectivity, validate_bpmn

from ._ir_fixtures import sequential

P = f"{{{BPMN}}}"


def test_not_wellformed_xml_rejected():
    result = validate_bpmn("<definitions><process></definitions>")  # mismatched tags
    assert not result.ok
    assert any("well-formed" in e for e in result.errors)


def test_schema_invalid_element_rejected():
    good = BpmnCompiler().compile(sequential()).bpmn_xml
    broken = good.replace("<bpmn:task ", "<bpmn:notAThing ", 1)
    result = validate_bpmn(broken)
    assert not result.ok
    assert result.errors


def _wrap(inner: str) -> str:
    return (
        f'<definitions xmlns="{BPMN}" id="d" targetNamespace="t">'
        f'<process id="p" isExecutable="false">{inner}</process></definitions>'
    )


def test_missing_end_event_detected():
    xml = _wrap(
        '<startEvent id="s"/><task id="a"/>'
        '<sequenceFlow id="f" sourceRef="s" targetRef="a"/>'
    )
    result = check_connectivity(xml)
    assert not result.ok
    assert "no end event" in result.errors


def test_orphan_node_detected():
    xml = _wrap('<startEvent id="s"/><endEvent id="e"/><task id="orphan"/>'
                '<sequenceFlow id="f" sourceRef="s" targetRef="e"/>')
    result = check_connectivity(xml)
    assert not result.ok
    assert any("orphan" in msg for msg in result.errors)


def test_unreachable_node_detected():
    xml = _wrap(
        '<startEvent id="s"/><task id="a"/><task id="b"/><endEvent id="e"/>'
        '<sequenceFlow id="f1" sourceRef="s" targetRef="a"/>'
        '<sequenceFlow id="f2" sourceRef="a" targetRef="e"/>'
        '<sequenceFlow id="f3" sourceRef="b" targetRef="b"/>'  # b loops on itself
    )
    result = check_connectivity(xml)
    assert not result.ok
    assert any("unreachable" in msg for msg in result.errors)


def test_valid_model_passes_both():
    xml = BpmnCompiler().compile(sequential()).bpmn_xml
    assert validate_bpmn(xml).ok
    assert check_connectivity(xml).ok
