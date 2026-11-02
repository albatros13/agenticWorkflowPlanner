"""Golden tests against the vendored data-governance framework (Ericmolenaar/Scattered-Directive).

The upstream scenarios mark expected-false queries in comments ("// False", "company-b fails",
"does not hold", "is terminated"). The reference simulator must yield exactly those results, which
is what lets us trust it to check LLM-generated specifications.
"""
import pytest

from agents.text_to_eflint.generation import check_spec, render_files
from agents.text_to_eflint.generation.convert import scenario_from_program
from agents.text_to_eflint.generation.reference import reference_path, reference_spec
from agents.text_to_eflint.generation.simulator import Simulator
from agents.text_to_eflint.generation.syntax import parse_file, parse_program

# Line numbers of the queries the scenario comments say are false.
EXPECTED_FALSE = {
    1: {33, 34, 35, 60, 61, 62, 82, 83, 84},
    2: {9, 21, 28, 36, 43, 51, 57, 65, 71, 79, 85, 93, 96, 99},
    3: {32, 52, 58, 65, 71, 78, 81},
    4: {9, 43, 50, 58, 65, 73, 80, 88, 89, 92},
    5: {66, 67, 68, 122, 123, 124},
}


@pytest.mark.parametrize("n", sorted(EXPECTED_FALSE))
def test_simulator_reproduces_upstream_scenario_expectations(n):
    path = str(reference_path("scenarios", f"Scenario-{n}.eflint").resolve())
    prog = parse_file(path)
    sim = Simulator.from_decls(prog.decls)
    false_lines, disabled = set(), []
    for st in prog.statements:
        if st.kind == "assert":
            sim.postulate(st.term)
        elif st.kind == "retract":
            sim.postulate(st.term, value=False)
        elif st.kind == "perform":
            inst, enabled = sim.perform(st.term)
            if not enabled:
                disabled.append(str(inst))
        elif st.kind == "query" and st.source == path:
            if not sim.query(st.expr):
                false_lines.add(st.line)
            if "false" in st.trailing.lower():  # every explicit "// False" is in the expected set
                assert st.line in EXPECTED_FALSE[n]
    assert false_lines == EXPECTED_FALSE[n]
    assert disabled == []  # upstream scenarios only perform enabled acts


def test_reference_spec_is_valid_and_round_trips_through_rendering(tmp_path):
    spec = reference_spec((1, 2, 3, 4, 5))
    report = check_spec(spec)
    assert report.errors == []
    assert all(s.passed for s in report.scenarios) and len(report.scenarios) == 5
    assert {f.code for f in report.warnings} <= {"guard_on_atomic", "no_source"}

    files = render_files(spec)
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    for n, original in enumerate(spec.scenarios, 1):
        path = str((tmp_path / f"Scenario-{n}.eflint").resolve())
        again = scenario_from_program(parse_file(path), path, name=original.name)  # literal ?/?! semantics
        assert [(s.kind, s.term, s.query, s.expect) for s in again.steps] == \
               [(s.kind, s.term, s.query, s.expect) for s in original.steps]
    # The single-file rendering parses on its own and declares everything.
    assert len(parse_program(files["spec.eflint"]).decls) == len(spec.declaration_names())


def test_rendered_norms_follow_reference_layout():
    norms = render_files(reference_spec())["Norms.eflint"]
    assert norms.startswith('#require "Ontology.eflint".')
    assert "// GDPR Art. 5(1)(b) - Purpose Limitation\nFact purpose-limitation Identified by controller * purpose." in norms
    assert ("    Holds when consent(data-subject, controller, purpose, data)\n"
            "        && personal-data-processed-lawfully(controller)") in norms
