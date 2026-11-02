"""Regulatory text -> validated, executable eFLINT, via an LLM draft + check/repair loop.

1. *(optional)* label the normative sentences with the FlintFiller SRL labeller and pass the
   resulting act frames to the model as hints;
2. draft a structured :class:`EflintSpec` (few-shot: the vendored reference framework; optionally
   extending an existing ``.eflint`` specification);
3. check it (:func:`.checks.check_spec`: syntax, names, typing, grounding in the source text,
   scenario execution) and, while errors remain, send the findings back for repair;
4. render the ``.eflint`` modules.

The LLM is reached through :class:`LLMProvider`, so any provider (or a scripted mock) works.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import ValidationError

from agents.text_to_bpmn.pipeline.llm.base import LLMError, LLMProvider

from .checks import Report, check_spec
from .convert import program_to_spec
from .prompts import SPEC_SCHEMA, SYSTEM, TASK_DRAFT, TASK_REPAIR, draft_prompt, repair_prompt
from .reference import reference_spec
from .render import render_files
from .spec import EflintSpec
from .syntax import Resolver, parse_program

log = logging.getLogger(__name__)

MAX_INPUT_CHARS = 60_000
_NORMATIVE = re.compile(
    r"\b(shall|must|may|is (?:entitled|obliged|required|permitted)|has the right|are (?:entitled|obliged|required))\b",
    re.IGNORECASE,
)
_SENT_SPLIT = re.compile(r"(?<=[.;:!?])\s+|\n{1,}")


@dataclass
class GenerationResult:
    status: Literal["succeeded", "needs_review"]
    spec: EflintSpec
    report: Report
    files: dict[str, str]
    rounds: int  # repair rounds used
    history: list[dict] = field(default_factory=list)  # per-round summaries
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "rounds": self.rounds,
            "summary": self.report.summary(),
            "findings": [f.model_dump(mode="json") for f in self.report.findings],
            "scenarios": [s.model_dump(mode="json") for s in self.report.scenarios],
            "spec": self.spec.model_dump(mode="json"),
            "files": self.files,
            "history": self.history,
            "notes": self.notes,
        }


def normative_sentences(text: str, limit: int) -> list[str]:
    sents = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    return [s for s in sents if _NORMATIVE.search(s) and len(s) > 20][:limit]


def parse_base_spec(text: str, *, resolver: Resolver | None = None) -> EflintSpec:
    """An existing ``.eflint`` specification to extend (declarations + ``+fact`` statements)."""
    return program_to_spec(parse_program(text, resolver=resolver))


class EflintGenerator:
    """Parameters
    ----------
    provider:
        Any :class:`LLMProvider`.
    max_repair_rounds:
        Upper bound on repair calls after the draft (0 disables repair).
    use_reference:
        Include the reference framework as a worked example (recommended; ~6k tokens).
    srl_hints / max_srl_sentences / language:
        Reuse the FlintFiller SRL labeller on up to ``max_srl_sentences`` normative sentences
        (one LLM call each) and pass the act frames as hints.
    """

    def __init__(
        self,
        provider: LLMProvider,
        *,
        max_repair_rounds: int = 2,
        use_reference: bool = True,
        srl_hints: bool = False,
        max_srl_sentences: int = 15,
        language: str = "en",
        max_tokens: int = 16_000,
    ):
        self.provider = provider
        self.max_repair_rounds = max_repair_rounds
        self.use_reference = use_reference
        self.srl_hints = srl_hints
        self.max_srl_sentences = max_srl_sentences
        self.language = language
        self.max_tokens = max_tokens

    def _frame_hints(self, text: str, notes: list[str]) -> list[dict]:
        from agents.text_to_eflint.labelling import FlintFrameLabeller  # noqa: PLC0415

        hints = []
        try:
            labeller = FlintFrameLabeller(self.provider, language=self.language)
            for sent in normative_sentences(text, self.max_srl_sentences):
                frame = labeller.label_frame(sent)
                if frame["action"]:
                    hints.append({"sentence": sent, **{k: frame[k] for k in ("actor", "action", "object", "recipient")}})
        except (LLMError, ValueError) as exc:  # hints are optional: never fail generation on them
            notes.append(f"SRL hints skipped: {exc}")
        return hints

    def _call(self, task: str, prompt: str, *, scenarios: bool = True) -> EflintSpec:
        result = self.provider.generate_json(
            task=task, system=SYSTEM, prompt=prompt, schema=SPEC_SCHEMA, max_tokens=self.max_tokens,
        )
        data = {k: v for k, v in (result.data or {}).items() if v is not None}
        try:
            spec = EflintSpec.model_validate(data)
        except ValidationError as exc:
            raise LLMError(f"model output does not match the eFLINT spec schema: {exc}") from exc
        if not scenarios:
            spec.scenarios = []
        return spec

    def generate(
        self,
        text: str,
        *,
        title: str = "",
        domain_context: str = "",
        base_spec: EflintSpec | None = None,
        scenarios: bool = True,
    ) -> GenerationResult:
        if not text or not text.strip():
            raise ValueError("text is required")
        if len(text) > MAX_INPUT_CHARS:
            raise ValueError(
                f"text is {len(text)} characters (limit {MAX_INPUT_CHARS}); select the relevant "
                f"articles and generate them separately (pass the result as base_spec to extend)"
            )
        notes: list[str] = []
        hints = self._frame_hints(text, notes) if self.srl_hints else None
        prompt = draft_prompt(
            text, reference=reference_spec() if self.use_reference else None, base=base_spec,
            domain_context=domain_context, title=title, frame_hints=hints, scenarios=scenarios,
        )
        spec = self._call(TASK_DRAFT, prompt, scenarios=scenarios)
        report = check_spec(spec, source_text=text)
        history = [{"round": 0, **report.summary()}]
        rounds = 0
        while report.errors and rounds < self.max_repair_rounds:
            rounds += 1
            log.info("eflint repair round %d: %d error(s)", rounds, len(report.errors))
            candidate = self._call(TASK_REPAIR, repair_prompt(text, spec, report), scenarios=scenarios)
            cand_report = check_spec(candidate, source_text=text)
            history.append({"round": rounds, **cand_report.summary()})
            if len(cand_report.errors) <= len(report.errors):  # never accept a worse repair
                spec, report = candidate, cand_report
            else:
                notes.append(f"repair round {rounds} made things worse ({len(cand_report.errors)} errors); kept previous version")
        status = "succeeded" if report.ok else "needs_review"
        return GenerationResult(status=status, spec=spec, report=report, files=render_files(spec),
                                rounds=rounds, history=history, notes=notes)
