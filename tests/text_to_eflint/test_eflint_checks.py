"""Static checks, grounding and scenario execution on generated specifications."""
from agents.text_to_eflint.generation import EflintSpec, check_spec

from ._eflint_samples import TEXT, spec


def _codes(report, severity="error"):
    return {f.code for f in report.findings if f.severity.value == severity}


def test_sample_spec_is_valid_grounded_and_its_scenario_passes():
    report = check_spec(EflintSpec.model_validate(spec()), source_text=TEXT)
    assert report.ok, report.findings
    assert "ungrounded_quote" not in _codes(report, "warning")
    [sc] = report.scenarios
    assert sc.passed and [s.actual for s in sc.steps if s.kind == "perform"] == [True, True, True, False]


def test_name_type_arity_and_binding_errors_are_reported():
    s = spec()
    s["acts"][2]["holds_when"] = "consent(data-subject, controller) && unknown(controller) && consent(x, controller, purpose)"
    s["acts"][2]["creates"] = ["give-consent()"]
    s["acts"][1]["actor"] = "citizen"
    s["facts"].append({"name": "Bad Name", "identified_by": ["String"], "source": "x"})
    s["facts"].append({"name": "consent", "identified_by": ["String"], "source": "x"})
    report = check_spec(EflintSpec.model_validate(s))
    assert {"arity", "unknown_type", "unbound_variable", "bad_effect", "bad_name", "duplicate"} <= _codes(report)
    assert report.scenarios == []  # not executed while declarations are broken


def test_type_mismatch_conversion_and_syntax_errors():
    s = spec()
    s["acts"][2]["holds_when"] = "consent(controller, controller, 1) && controller(data-subject) != controller"
    s["acts"][0]["creates"] = ["consent("]
    report = check_spec(EflintSpec.model_validate(s))
    assert {"type_mismatch", "syntax"} <= _codes(report)  # 1 (Int) for purpose (String); bad term
    conversions = [f for f in report.warnings if f.code == "implicit_conversion"]
    assert len(conversions) == 1 and "'controller' but the parameter is 'data-subject'" in conversions[0].message


def test_failing_scenario_expectation_is_an_error():
    s = spec()
    s["scenarios"][0]["steps"][1]["expect"] = False  # consent does hold after give-consent
    report = check_spec(EflintSpec.model_validate(s))
    assert _codes(report) == {"scenario_failed"}
    assert report.scenarios[0].passed is False
    assert "expects False" in report.errors[0].message


def test_disabled_act_performed_as_enabled_is_an_error():
    s = spec()
    s["scenarios"][0]["steps"][-1]["expect"] = True
    report = check_spec(EflintSpec.model_validate(s))
    assert any("is not enabled" in f.message for f in report.errors)


def test_bad_ground_terms_in_initial_state_and_scenarios():
    s = spec(initial_state=[{"term": "consent(alice, \"acme\", \"marketing\")"}])
    s["scenarios"][0]["steps"].append({"kind": "query", "term": 'consent("alice")', "query": "holds"})
    s["scenarios"][0]["steps"].append({"kind": "query", "term": 'give-consent("alice", "acme", "m")', "query": "violated"})
    report = check_spec(EflintSpec.model_validate(s))
    assert {"unbound_variable"} <= _codes(report)
    s.pop("initial_state")
    s["initial_state"] = []
    report = check_spec(EflintSpec.model_validate(s))
    assert {"arity", "bad_query", "bad_statement"} & _codes(report)


def test_grounding_and_duty_coverage_warnings():
    s = spec()
    s["acts"][0]["source_quote"] = "the controller may sell the data"  # not in the text
    s["duties"] = [{"name": "inform", "holder": "controller", "claimant": "data-subject", "source": "Art. 13"}]
    report = check_spec(EflintSpec.model_validate(s), source_text=TEXT)
    warnings = _codes(report, "warning")
    assert {"ungrounded_quote", "duty_never_violated", "duty_never_created"} <= warnings
    assert report.ok  # warnings do not block


def test_violated_duty_semantics():
    s = spec()
    s["duties"] = [{"name": "erase-data", "holder": "controller", "claimant": "data-subject", "related_to": ["purpose"],
                    "violated_when": "processed(controller, data-subject, purpose)", "source": "GDPR Art. 17"}]
    s["acts"][1]["creates"] = ["erase-data(controller, data-subject, purpose)"]
    s["scenarios"][0]["steps"].append(
        {"kind": "query", "term": 'erase-data("acme", "alice", "marketing")', "query": "violated", "expect": True})
    report = check_spec(EflintSpec.model_validate(s))
    assert report.ok, report.findings
