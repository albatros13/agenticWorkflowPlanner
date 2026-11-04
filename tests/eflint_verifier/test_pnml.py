import xml.etree.ElementTree as ET

from agents.eflint_verifier.pnml import PNML_NS, PTNET_TYPE, normalize_pnml, pn_transitions, slug

# What agents/petri_net_generator writes: unnamespaced core model, label names, finalmarkings.
PM4PY_STYLE = """<?xml version='1.0' encoding='UTF-8'?>
<pnml><net id="n" type="http://www.pnml.org/version-2009/grammar/pnmlcoremodel">
  <page id="page0">
    <place id="source"><name><text>source</text></name><initialMarking><text>1</text></initialMarking></place>
    <place id="p_G1"><name><text>p_G1</text></name></place>
    <transition id="A"><name><text>Submit request</text></name></transition>
    <transition id="B"><name><text>Submit  request!</text></name></transition>
    <transition id="T"><name><text>submit-request</text></name></transition>
    <transition id="G2"><name><text>G2</text></name>
      <toolspecific tool="ProM" version="6.4" activity="$invisible$" localNodeID="G2"/></transition>
    <arc id="a1" source="source" target="A"/><arc id="a2" source="A" target="p_G1"/>
  </page>
  <finalmarkings><marking><place idref="p_G1"><text>1</text></place></marking></finalmarkings>
</net></pnml>"""


def test_slug_is_valid_for_pnml_and_analysis():
    assert slug("Submit request") == "submit-request"
    assert slug("Step 1") == "step-1_"         # analysis names may not end in a digit
    assert slug("1st check") == "t-1st-check"  # nor start with one
    assert slug("Réception") == "r-ception"
    assert slug("   ") == "node"


def test_pm4py_style_pnml_is_converted_to_the_standard_profile():
    out = normalize_pnml(PM4PY_STYLE)
    root = ET.fromstring(out.pnml)
    assert root.tag == f"{{{PNML_NS}}}pnml"
    assert root.find(f"{{{PNML_NS}}}net").get("type") == PTNET_TYPE
    assert "finalmarkings" not in out.pnml
    assert any("finalmarkings" in c for c in out.changes)
    # Valid names are kept; collisions get a letter suffix; arcs/IDs are untouched.
    assert out.transitions == ["submit-request-b", "submit-request-c", "submit-request", "g2_"]
    assert out.renamed == {"Submit request": "submit-request-b", "Submit  request!": "submit-request-c",
                           "G2": "g2_", "p_G1": "p_g1_"}
    assert out.places == ["source", "p_g1_"]
    assert 'source="A"' in out.pnml


def test_valid_namespaced_pnml_is_returned_unchanged():
    doc = (f'<pnml xmlns="{PNML_NS}"><net id="n" type="{PTNET_TYPE}"><page id="p">'
           '<place id="p1"><name><text>ready</text></name></place>'
           '<transition id="t1"><name><text>finish</text></name></transition></page></net></pnml>')
    out = normalize_pnml(doc)
    assert out.pnml == doc and out.renamed == {} and out.changes == []
    assert out.transitions == ["finish"] and out.places == ["ready"]


def test_malformed_pnml_raises_value_error():
    import pytest

    with pytest.raises(ValueError, match="well-formed"):
        normalize_pnml("<pnml><net>")


def test_pn_transitions():
    assert pn_transitions("place a = 1\ntransition begin: a -> b\n  transition end-it: b -> c\n") == ["begin", "end-it"]
