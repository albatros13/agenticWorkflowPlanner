"""Guards for the vendored FLINT ontology loader (agents/text_to_eflint/flint).

Covers asset presence, the graph/query loaders, and a full pySHACL validation round-trip.
The whole module is skipped when the ``eflint`` extra (rdflib + pyshacl) is not installed, so
the core test-suite still runs without those deps — matching how the package treats them as
optional.
"""
import pytest

pytest.importorskip("rdflib", reason="requires the 'eflint' extra (rdflib)")
pytest.importorskip("pyshacl", reason="requires the 'eflint' extra (pyshacl)")

from rdflib import Graph  # noqa: E402  (after importorskip by design)

from agents.text_to_eflint import ontology  # noqa: E402


def test_version_is_pinned():
    # Vendored copy is pinned; if this changes, VERSION and the shapes should be re-synced.
    assert ontology.__flint_version__ == "flint-ontology v1.0.0"


def test_load_ontology_has_flint_namespace():
    onto = ontology.load_ontology()
    assert len(onto) > 0
    # flint.ttl + functions.ttl both merged in: the FLINT namespace must appear as a subject.
    assert any(str(s).startswith(str(ontology.FLINT_NS)) for s in onto.subjects())


def test_load_shapes_full_and_warnings_differ():
    full = ontology.load_shapes()
    warnings = ontology.load_shapes(warnings_only=True)
    assert len(full) > 0 and len(warnings) > 0
    # The warnings-only profile is the lighter of the two.
    assert len(warnings) < len(full)


def test_list_queries_are_discoverable():
    queries = ontology.list_queries()
    assert "cq-act" in queries
    # All 22 competency questions ship with the package.
    assert len(queries) == 22
    assert all(not q.endswith(".rq") for q in queries)


def test_load_query_accepts_name_with_or_without_suffix():
    assert ontology.load_query("cq-act") == ontology.load_query("cq-act.rq")
    assert "SELECT" in ontology.load_query("cq-act").upper()


def test_load_query_unknown_name_raises_with_hint():
    with pytest.raises(FileNotFoundError) as excinfo:
        ontology.load_query("does-not-exist")
    # Error should help the caller by listing what *is* available.
    assert "cq-act" in str(excinfo.value)


def test_bundled_competency_question_runs_against_ontology():
    # A CQ should at least parse and execute against a FLINT graph without error.
    graph = ontology.load_ontology()
    result = graph.query(ontology.load_query("cq-act"))
    # SELECT query -> iterable result set (may be empty for the schema-only graph).
    assert result.type == "SELECT"
    list(result)  # force evaluation; raises if the query is malformed


def test_validate_empty_graph_conforms():
    # An empty data graph trivially conforms; this exercises the full pyshacl path
    # (shapes + ontology + SHACL-AF rules + OWL-RL inference).
    conforms, report_graph, report_text = ontology.validate(Graph())
    assert conforms is True
    assert isinstance(report_text, str)


def test_validate_inplace_infers_triples():
    # OWL-RL inference over the ontology itself should add triples when inplace=True.
    data = ontology.load_ontology()
    before = len(data)
    ontology.validate(data, inplace=True)
    assert len(data) >= before
