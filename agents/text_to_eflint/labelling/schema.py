"""The core reusable IP: the FLINT semantic-role schema + system instructions.

Ported verbatim (role definitions & wording) from FlintFiller-SRL's
``label_text_function_call_gpt.py`` (``tag_function`` + ``system_instructions_srl``),
re-shaped into a plain JSON Schema so it drops straight into the provider-agnostic
``LLMProvider.generate_json(..., schema=FLINT_ROLE_SCHEMA)`` call. Every modern
provider maps a JSON-schema tool/function onto this, so it is not OpenAI-specific.
"""
from __future__ import annotations

# Stable task label: routes ScriptedLLMProvider responses and names the tool the
# Anthropic/OpenAI adapters force the model to call (e.g. ``emit_flint_srl``).
TASK = "flint_srl"

# The five FLINT semantic roles, in output order.
ROLE_KEYS = ("action", "actor", "object", "recipient", "other")

FLINT_ROLE_SCHEMA: dict = {
    "type": "object",
    "description": (
        "Classify all words in a sentence as part of the action, actor, object, "
        "recipient, or other. Include determiners, adjectives, prepositions, "
        "complementisers, negations and phrasal verbs. Exclude adverbs. An action "
        "consists of the main verb of the sentence and its auxiliaries and modals."
    ),
    "properties": {
        "action": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "All words classified as the action of the sentence. An action "
                "consists of the main verb of the sentence and its auxiliaries and modals."
            ),
        },
        "actor": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "All words classified as the actor: the main agent who interacts "
                "with the action; the volitional causer of the event."
            ),
        },
        "object": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "All words classified as the object: the direct object of the action; "
                "the entity moved by, or undergoing the effect of, the action."
            ),
        },
        "recipient": {
            "type": "array",
            "items": {"type": "string"},
            "description": "All words classified as the recipient: the benefactor of the action.",
        },
        "other": {
            "type": "array",
            "items": {"type": "string"},
            "description": "All words not classified into the other categories.",
        },
    },
    "required": list(ROLE_KEYS),
}

SYSTEM_INSTRUCTIONS = (
    "You are an assistant that classifies all words in the main clause of a "
    "sentence to the roles 'action', 'actor', 'object', 'recipient', or 'other'. "
    "All words in a subordinate clause should be classified as 'other'. "
    "Do not leave words unclassified, and classify each word only once. "
    "Check that all words are classified before returning the result."
)
