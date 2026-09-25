"""LLM-based FLINT act-frame labelling (ported from FlintFiller-SRL's GPT approach).

Runs entirely on the ScriptedLLMProvider — no network, no API keys — exercising the
provider-agnostic labeller, the roles->frame mapping, and the frame templates.
"""
from agents.text_to_bpmn.pipeline.llm.mock import ScriptedLLMProvider
from agents.text_to_eflint.labelling import (
    FLINT_ROLE_SCHEMA,
    FlintFrameLabeller,
    create_empty_act_frame,
    frames_from_sentences,
    role_dict_to_act_frame,
)
from agents.text_to_eflint.labelling.schema import ROLE_KEYS, TASK

MINISTER_ROLES = {
    "action": ["shall", "grant"],
    "actor": ["The", "minister"],
    "object": ["a", "residence", "permit"],
    "recipient": ["to", "the", "applicant"],
    "other": ["."],
}


def test_role_dict_to_act_frame_concatenates_action_and_object():
    frame = role_dict_to_act_frame(MINISTER_ROLES)
    assert frame["action"] == "shall grant"
    assert frame["object"] == "a residence permit"
    assert frame["actor"] == "The minister"
    assert frame["recipient"] == "to the applicant"
    # "act" is the naive action+object concatenation (original behaviour).
    assert frame["act"] == "shall grant a residence permit"
    # Placeholders from the empty template survive untouched.
    assert frame["preconditions"] == {"expression": "LITERAL", "operand": True}
    assert frame["sources"] == [] and frame["explanation"] == ""


def test_empty_act_frame_is_the_flint_contract():
    empty = create_empty_act_frame()
    assert set(empty) == {
        "act", "actor", "action", "object", "recipient",
        "preconditions", "create", "terminate", "sources", "explanation",
    }


def test_labeller_tags_roles_via_provider():
    provider = ScriptedLLMProvider({TASK: MINISTER_ROLES})
    labeller = FlintFrameLabeller(provider, language="en")

    roles = labeller.tag_roles("The minister shall grant a residence permit to the applicant .")
    assert roles == MINISTER_ROLES

    # The call is routed by the stable task label, and few-shot examples reach the prompt.
    call = provider.calls[-1]
    assert call.task == TASK
    assert call.schema is FLINT_ROLE_SCHEMA
    assert "Examples:" in call.prompt and "shall calculate" in call.prompt  # EN gold example


def test_normalise_fills_missing_roles_and_splits_strings():
    # Model omits 'recipient' and returns 'action' as a bare string -> both coerced.
    provider = ScriptedLLMProvider({TASK: {"action": "shall grant", "actor": ["X"], "object": [], "other": []}})
    roles = FlintFrameLabeller(provider).tag_roles("X shall grant .")
    assert set(roles) == set(ROLE_KEYS)
    assert roles["action"] == ["shall", "grant"]
    assert roles["recipient"] == []


def test_frames_from_sentences_drops_empty_action_frames():
    empty_action = {"action": [], "actor": ["Nobody"], "object": [], "recipient": [], "other": ["."]}
    provider = ScriptedLLMProvider({TASK: [MINISTER_ROLES, empty_action]})
    labeller = FlintFrameLabeller(provider, language="en")

    flint = labeller.frames_from_sentences(["a sentence", "another sentence"])
    assert flint["facts"] == [] and flint["duties"] == []
    # Second sentence had an empty action -> dropped.
    assert len(flint["acts"]) == 1
    assert flint["acts"][0]["action"] == "shall grant"


def test_frames_from_sentences_helper_matches_method():
    provider = ScriptedLLMProvider({TASK: MINISTER_ROLES})
    labeller = FlintFrameLabeller(provider, language="en")
    direct = frames_from_sentences(["one sentence"], labeller.tag_roles)
    assert direct["acts"][0]["act"] == "shall grant a residence permit"


def test_unknown_language_without_examples_raises():
    import pytest

    with pytest.raises(ValueError, match="No built-in few-shot examples"):
        FlintFrameLabeller(ScriptedLLMProvider({}), language="fr")
