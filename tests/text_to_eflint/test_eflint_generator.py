"""LLM draft -> check -> repair loop, offline via the scripted provider."""
import pytest

from agents.text_to_bpmn.pipeline.llm.base import LLMError
from agents.text_to_bpmn.pipeline.llm.mock import ScriptedLLMProvider
from agents.text_to_eflint.generation import EflintGenerator, parse_base_spec
from agents.text_to_eflint.generation.generator import MAX_INPUT_CHARS, normative_sentences
from agents.text_to_eflint.generation.prompts import TASK_DRAFT, TASK_REPAIR
from agents.text_to_eflint.labelling.schema import TASK as SRL_TASK

from ._eflint_samples import TEXT, spec


def _broken():
    s = spec()
    s["acts"][2]["holds_when"] = "consent(data-subject, controller)"  # arity error
    return s


def test_valid_draft_needs_no_repair_and_renders_modules():
    provider = ScriptedLLMProvider({TASK_DRAFT: spec()})
    result = EflintGenerator(provider).generate(TEXT, domain_context="newsletter sign-up")
    assert result.status == "succeeded" and result.rounds == 0
    assert set(result.files) == {"Ontology.eflint", "Norms.eflint", "Init.eflint", "Scenario-1.eflint", "spec.eflint"}
    assert "Act withdraw-consent" in result.files["Norms.eflint"]
    assert '?!Enabled(process-personal-data("acme", "alice", "marketing")).' in result.files["Scenario-1.eflint"]
    [call] = provider.calls
    # Few-shot reference, application context and the text are all in the draft prompt.
    assert "REFERENCE EXAMPLE" in call.prompt and "collect-personal-data" in call.prompt
    assert "newsletter sign-up" in call.prompt and "withdraw his or her consent" in call.prompt
    assert call.schema["required"][:4] == ["title", "facts", "duties", "acts"]


def test_findings_are_fed_back_and_repair_is_accepted():
    provider = ScriptedLLMProvider({TASK_DRAFT: _broken(), TASK_REPAIR: spec()})
    result = EflintGenerator(provider).generate(TEXT)
    assert result.status == "succeeded" and result.rounds == 1
    assert [h["errors"] for h in result.history] == [1, 0]
    repair_call = provider.calls[1]
    assert repair_call.task == TASK_REPAIR
    assert "process-personal-data" in repair_call.prompt and "takes 3 argument(s)" in repair_call.prompt


def test_unrepairable_spec_returns_needs_review_with_findings():
    provider = ScriptedLLMProvider({TASK_DRAFT: _broken(), TASK_REPAIR: _broken()})
    result = EflintGenerator(provider, max_repair_rounds=2).generate(TEXT)
    assert result.status == "needs_review" and result.rounds == 2
    assert result.report.errors and result.files["Norms.eflint"]  # still rendered for inspection


def test_worse_repair_is_rejected():
    worse = _broken()
    worse["acts"][0]["creates"] = ["nope()"]
    worse["acts"][1]["terminates"] = ["nope()"]
    provider = ScriptedLLMProvider({TASK_DRAFT: _broken(), TASK_REPAIR: worse})
    result = EflintGenerator(provider, max_repair_rounds=1).generate(TEXT)
    assert len(result.report.errors) == 1
    assert "made things worse" in result.notes[0]


def test_scenarios_can_be_disabled():
    provider = ScriptedLLMProvider({TASK_DRAFT: spec()})
    result = EflintGenerator(provider).generate(TEXT, scenarios=False)
    assert result.spec.scenarios == [] and "Return an empty scenarios list." in provider.calls[0].prompt


def test_srl_hints_reuse_the_flint_labeller():
    roles = {"action": ["shall", "have"], "actor": ["The", "data", "subject"], "object": ["the", "right"],
             "recipient": [], "other": []}
    provider = ScriptedLLMProvider({SRL_TASK: roles, TASK_DRAFT: spec()})
    EflintGenerator(provider, srl_hints=True).generate(TEXT)
    srl_calls = [c for c in provider.calls if c.task == SRL_TASK]
    assert len(srl_calls) == len(normative_sentences(TEXT, 15)) == 2
    assert "CANDIDATE ACT FRAMES" in provider.calls[-1].prompt and "shall have" in provider.calls[-1].prompt


def test_base_spec_is_passed_for_extension():
    base = parse_base_spec("Fact controller Identified by String.\nFact purpose Identified by String.")
    provider = ScriptedLLMProvider({TASK_DRAFT: spec()})
    EflintGenerator(provider, use_reference=False).generate(TEXT, base_spec=base)
    prompt = provider.calls[0].prompt
    assert "EXISTING SPECIFICATION" in prompt and "REFERENCE EXAMPLE" not in prompt


def test_input_limits_and_schema_violations():
    gen = EflintGenerator(ScriptedLLMProvider({TASK_DRAFT: {"facts": "not a list"}}))
    with pytest.raises(ValueError):
        gen.generate("   ")
    with pytest.raises(ValueError):
        gen.generate("x" * (MAX_INPUT_CHARS + 1))
    with pytest.raises(LLMError):
        gen.generate(TEXT)
