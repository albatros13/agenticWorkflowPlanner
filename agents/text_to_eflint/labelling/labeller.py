"""FLINT act-frame labeller built on the shared provider-agnostic LLM layer.

This is the "provider-agnostic LLM layer" the reuse plan (§4.3) calls for — except
we don't add ``litellm``: the repo already ships :class:`LLMProvider` (Anthropic,
OpenAI, scripted-mock, local) with a structured ``generate_json`` API, so the same
adapter powers text->BPMN and text->eFLINT. Swapping or mixing models needs no change
here — just a different provider.
"""
from __future__ import annotations

import json

from agents.text_to_bpmn.pipeline.llm.base import LLMProvider

from .few_shot_examples import EXAMPLES_BY_LANGUAGE
from .roles_to_frame import frames_from_sentences, role_dict_to_act_frame
from .schema import FLINT_ROLE_SCHEMA, ROLE_KEYS, SYSTEM_INSTRUCTIONS, TASK


class FlintFrameLabeller:
    """Label sentences with FLINT semantic roles and assemble act frames.

    Parameters
    ----------
    provider:
        Any :class:`LLMProvider`. Determinism (temperature 0), retries and JSON
        validation live in the provider layer, so they are handled once for all agents.
    language:
        ``"en"`` or ``"nl"`` — selects the built-in gold few-shot examples.
    examples:
        Override the few-shot examples entirely (list of ``{"sentence", "roles"}``).
    max_tokens:
        Upper bound for the role response.
    """

    def __init__(
        self,
        provider: LLMProvider,
        *,
        language: str = "en",
        examples: list[dict] | None = None,
        max_tokens: int = 2048,
    ):
        self.provider = provider
        self.language = language
        if examples is None:
            if language not in EXAMPLES_BY_LANGUAGE:
                raise ValueError(
                    f"No built-in few-shot examples for language {language!r}; "
                    f"available: {sorted(EXAMPLES_BY_LANGUAGE)}. Pass examples=... to override."
                )
            examples = EXAMPLES_BY_LANGUAGE[language]
        self.examples = examples
        self.max_tokens = max_tokens

    def _build_prompt(self, sentence: str) -> str:
        """Render gold examples + the target sentence into a single prompt string.

        The original code used OpenAI ``example_user``/``example_assistant`` messages;
        folding them into the prompt keeps the same few-shot signal while working
        against the provider-neutral ``generate_json`` interface.
        """
        lines: list[str] = [
            "Classify every word of the sentence into the roles action, actor, "
            "object, recipient, or other. Examples:",
        ]
        for ex in self.examples:
            lines.append("")
            lines.append(f"Sentence: {ex['sentence'].strip()}")
            lines.append(f"Roles: {json.dumps(ex['roles'], ensure_ascii=False)}")
        lines.append("")
        lines.append("Now classify this sentence.")
        lines.append(f"Sentence: {sentence.strip()}")
        return "\n".join(lines)

    def tag_roles(self, sentence: str) -> dict:
        """Return ``{action, actor, object, recipient, other}`` role lists for one sentence."""
        result = self.provider.generate_json(
            task=TASK,
            system=SYSTEM_INSTRUCTIONS,
            prompt=self._build_prompt(sentence),
            schema=FLINT_ROLE_SCHEMA,
            max_tokens=self.max_tokens,
        )
        return self._normalise(result.data)

    def label_frame(self, sentence: str) -> dict:
        """Label one sentence and return a single FLINT act frame."""
        return role_dict_to_act_frame(self.tag_roles(sentence))

    def frames_from_sentences(self, sentences) -> dict:
        """Label many sentences into a complete FLINT format (acts filled)."""
        return frames_from_sentences(sentences, self.tag_roles)

    @staticmethod
    def _normalise(data: dict) -> dict:
        """Coerce the model output into all five role keys, each a list of strings."""
        roles: dict[str, list[str]] = {}
        for key in ROLE_KEYS:
            value = data.get(key, [])
            if value is None:
                value = []
            if isinstance(value, str):
                value = value.split()
            roles[key] = [str(w) for w in value]
        return roles
