"""Demo API: legal text -> FLINT semantic-role labelling (SRL) + act frames, and eFLINT generation.

POST /api/label splits the submitted text into sentences, runs each through the
provider-agnostic :class:`FlintFrameLabeller`, and returns, per sentence, the words
aligned to their roles (action/actor/object/recipient/other) plus the derived FLINT
act frame. The static frontend at ``/`` renders the labelling with per-role colours.

Mirrors the text->BPMN demo (``agents/text_to_bpmn/demo``): it selects a real LLM
provider via the shared ``api``-backed layer and surfaces failures to the UI.

POST /api/generate runs the agent's ``generate_eflint`` intent (LLM draft -> check -> repair) and
returns the specification, findings, scenario results and rendered ``.eflint`` files.

GET /api/runs lists the generations stored in the agent's storage (``runs/<run_id>/``) and
GET /api/runs/{run_id} reopens one (same shape as /api/generate, plus the saved ``input``).

GET /api/samples lists the regulatory text samples that come with a reference formalisation (the
DMI Afsprakenstelsel, see ``agents/text_to_eflint/evaluation/dmi/README.md``);
GET /api/samples/{id} returns one (text, application context, reference modules and example
scenario) and POST /api/samples/{id}/compare compares a generated spec with its reference.
"""
from __future__ import annotations

import re
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agents.text_to_bpmn.pipeline.config import Settings
from agents.text_to_bpmn.pipeline.llm.base import LLMError, LLMProvider
from agents.text_to_bpmn.shaping import select_provider
from agents.contract import AgentRequest
from agents.text_to_eflint.agent import GENERATE_EFLINT, TextToEflintHandler
from agents.text_to_eflint.evaluation import qa
from agents.text_to_eflint.evaluation.compare import compare
from agents.text_to_eflint.evaluation.samples import (DMI_COMMIT, DMI_REPOSITORY, articles, get_sample,
                                                      samples)
from agents.text_to_eflint.generation import EflintSpec
from agents.text_to_eflint.labelling import FlintFrameLabeller, role_dict_to_act_frame
from agents.text_to_eflint.labelling.schema import ROLE_KEYS

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Keep a demo request bounded (one LLM call per sentence).
MAX_SENTENCES = 40

app = FastAPI(title="Text-to-eFLINT labelling demo")

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n{1,}")
_TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)


class LabelRequest(BaseModel):
    text: str = Field(min_length=1)
    language: str = "en"  # "en" | "nl"


def _select_provider(settings: Settings) -> LLMProvider:
    """Pick a real provider for the demo (overridable in tests via monkeypatch)."""
    return select_provider(settings)


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]


def align_tokens(sentence: str, roles: dict) -> list[dict]:
    """Assign every word of the sentence to a role, preserving reading order.

    Each role list keeps the words in sentence order, so we greedily match the next
    queued word per role first, then fall back to any remaining occurrence. Words the
    model never returned default to 'other' so nothing renders unlabelled.
    """
    queues = {r: deque(roles.get(r, []) or []) for r in ROLE_KEYS}
    out: list[dict] = []
    for tok in _TOKEN.findall(sentence):
        assigned = None
        for r in ROLE_KEYS:  # in-order match (front of queue)
            q = queues[r]
            if q and q[0] == tok:
                q.popleft()
                assigned = r
                break
        if assigned is None:  # out-of-order fallback: any remaining occurrence
            for r in ROLE_KEYS:
                q = queues[r]
                if tok in q:
                    q.remove(tok)
                    assigned = r
                    break
        out.append({"word": tok, "role": assigned or "other"})
    return out


@app.post("/api/label")
def label(req: LabelRequest) -> dict:
    settings = Settings.from_env()
    try:
        provider = _select_provider(settings)
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    language = (req.language or "en").lower()
    try:
        labeller = FlintFrameLabeller(provider, language=language)
    except ValueError as exc:  # unknown language / no few-shot examples
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    sentences = split_sentences(req.text)
    truncated = len(sentences) > MAX_SENTENCES
    sentences = sentences[:MAX_SENTENCES]
    if not sentences:
        raise HTTPException(status_code=422, detail="No sentences found in the input text.")

    results = []
    try:
        for sent in sentences:
            roles = labeller.tag_roles(sent)
            frame = role_dict_to_act_frame(roles)
            results.append({
                "text": sent,
                "tokens": align_tokens(sent, roles),
                "roles": {r: roles.get(r, []) for r in ROLE_KEYS},
                "frame": frame,
                "has_act": bool(frame["action"]),
            })
    except LLMError as exc:
        raise HTTPException(status_code=422, detail=f"Labelling failed: {exc}") from exc

    # Assemble the FLINT format from the frames we already computed (acts with a
    # non-empty action, matching frames_from_sentences' rule) — no extra LLM calls.
    flint = {
        "acts": [r["frame"] for r in results if r["has_act"]],
        "facts": [],
        "duties": [],
    }

    return {
        "language": language,
        "provider": provider.name,
        "sentences": results,
        "flint": flint,
        "counts": {
            "sentences": len(results),
            "acts": len(flint["acts"]),
        },
        "truncated": truncated,
    }


class GenerateRequest(BaseModel):
    text: str = Field(min_length=1)
    domain_context: str = ""
    title: str = ""
    srl_hints: bool = False
    language: str = "en"
    max_repair_rounds: int = Field(default=2, ge=0, le=5)


@app.post("/api/generate")
def generate(req: GenerateRequest) -> dict:
    settings = Settings.from_env()
    try:
        provider = _select_provider(settings)
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    result = TextToEflintHandler(provider=provider).handle(
        AgentRequest(intent=GENERATE_EFLINT, payload=req.model_dump())
    )
    if not result.ok:
        raise HTTPException(status_code=422, detail=result.error)
    return result.output


def _store() -> TextToEflintHandler:
    """Handler used only to read stored runs (no provider is created)."""
    return TextToEflintHandler()


@app.get("/api/runs")
def runs() -> dict:
    items = _store().list_runs()
    return {"runs": items, "count": len(items)}


@app.get("/api/runs/{run_id}")
def run(run_id: str) -> dict:
    try:
        return _store().load_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc


@app.get("/api/samples")
def sample_list() -> dict:
    items = [{"id": s.id, "title": s.title, "language": s.language, "articles": s.articles} for s in samples()]
    return {"samples": items, "count": len(items)}


def _sample(sample_id: str):
    try:
        return get_sample(sample_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc


@app.get("/api/samples/{sample_id}")
def sample_detail(sample_id: str) -> dict:
    s = _sample(sample_id)
    arts = articles()
    return {
        "id": s.id, "title": s.title, "language": s.language, "text": s.text,
        "domain_context": s.domain_context,
        "articles": [{"id": a, "source": arts[a].source, "module": arts[a].module} for a in s.articles],
        "reference": {"files": s.reference_files(), "example": {"path": s.example, "source": s.example_source()}},
        "provenance": {"repository": DMI_REPOSITORY, "commit": DMI_COMMIT, "license": "Apache-2.0"},
    }


class CompareRequest(BaseModel):
    spec: dict


@app.post("/api/samples/{sample_id}/compare")
def sample_compare(sample_id: str, req: CompareRequest) -> dict:
    s = _sample(sample_id)
    try:
        spec = EflintSpec.model_validate(req.spec)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"not a generated spec: {exc}") from exc
    comparison = compare(spec, s.reference_files(), base=qa.base_sources(), articles=s.articles)
    return {**comparison.summary(), "details": comparison.details()}


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    # no-cache: browsers revalidate (cheap 304) instead of showing a stale page after an update
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=str(STATIC_DIR)), name="static")
