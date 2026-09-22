"""Demo API: natural-language process text -> extracted resources + BPMN diagram.

POST /api/generate runs the pipeline (ingest -> evidence -> IR -> terminology ->
BPMN/DMN) on submitted text and returns the BPMN XML plus the extracted resources
for display. The static frontend at ``/`` renders the diagram with bpmn-js.
"""
from __future__ import annotations

import os
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.config import REPO_ROOT, Settings
from src.llm.base import LLMError, LLMProvider
from src.llm.factory import get_provider
from src.pipeline.artifacts import ArtifactStore
from src.pipeline.runner import Pipeline
from src.validation.schema import check_connectivity, validate_bpmn, validate_dmn

STATIC_DIR = REPO_ROOT / "app" / "static"

app = FastAPI(title="Guideline-to-BPMN demo")


class GenerateRequest(BaseModel):
    text: str = Field(min_length=1)
    process_name: str = ""


def _select_provider(settings: Settings) -> LLMProvider:
    """Pick a real provider for the demo, preferring whatever key is configured."""
    name = os.getenv("LLM_PROVIDER", "").lower()
    if name in ("anthropic", "openai"):
        return get_provider(settings, provider=name)
    if os.getenv("ANTHROPIC_API_KEY"):
        return get_provider(settings, provider="anthropic")
    if os.getenv("OPENAI_API_KEY"):
        return get_provider(settings, provider="openai")
    raise LLMError("No LLM configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env.")


def _resources(evidence, process, annotations, model) -> dict:
    return {
        "actors": process.actors,
        "evidence": [
            {"id": e.id, "text": e.text, "section": e.section, "page": e.page}
            for e in evidence.items
        ],
        "elements": [
            {
                "id": el.id,
                "type": el.type.value,
                "name": el.name,
                "actor": el.actor,
                "status": el.status.value,
                "gateway_kind": el.gateway_kind.value if el.gateway_kind else None,
                "thresholds": [
                    f"{t.measure} {t.operator} {t.value}{t.value_high and ('..' + str(t.value_high)) or ''}"
                    f"{(' ' + t.unit) if t.unit else ''}"
                    for t in el.thresholds
                ],
                "temporal": [
                    f"{t.kind} {t.value} {t.unit}{(' ' + t.reference) if t.reference else ''}"
                    for t in el.temporal_constraints
                ],
                "evidence_refs": el.evidence_refs,
            }
            for el in process.elements
        ],
        "terminology": [
            {
                "element_name": a.element_name,
                "mappings": [
                    {
                        "surface_form": m.surface_form,
                        "normalized_label": m.normalized_label,
                        "ontology": m.ontology,
                        "concept_id": m.concept_id,
                        "match_type": m.match_type.value,
                        "confidence": m.confidence,
                    }
                    for m in a.mappings
                ],
            }
            for a in annotations
        ],
        "decisions": [
            {
                "id": d.id,
                "name": d.name,
                "inputs": d.inputs,
                "rules": [{"inputs": r.inputs, "output": r.output} for r in d.rules],
            }
            for d in model.graph.decisions
        ],
        "counts": {
            "evidence": len(evidence.items),
            "elements": len(process.elements),
            "relations": len(process.relations),
            "nodes": len(model.graph.nodes),
            "flows": len(model.graph.flows),
        },
    }


@app.post("/api/generate")
def generate(req: GenerateRequest) -> dict:
    settings = Settings.from_env()
    try:
        provider = _select_provider(settings)
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    run_id = uuid.uuid4().hex[:12]
    store = ArtifactStore.new_run(settings.runs_dir, run_id)
    pipe = Pipeline(provider, store=store, settings=settings)

    try:
        pipe.ingest_text(req.text)
        evidence = pipe.extract_evidence()
        process = pipe.interpret(process_name=req.process_name)
        annotations = pipe.ground_terminology()
        model = pipe.compile_bpmn()
    except Exception as exc:  # surface pipeline/LLM failures to the UI
        raise HTTPException(status_code=422, detail=f"{type(exc).__name__}: {exc}")

    vb, vd = validate_bpmn(model.bpmn_xml), (validate_dmn(model.dmn_xml) if model.dmn_xml else None)
    cc = check_connectivity(model.bpmn_xml)

    return {
        "run_id": run_id,
        "provider": provider.name,
        "bpmn_xml": model.bpmn_xml,
        "dmn_xml": model.dmn_xml,
        "resources": _resources(evidence, process, annotations, model),
        "validation": {
            "bpmn_valid": vb.ok,
            "bpmn_errors": vb.errors[:5],
            "dmn_valid": vd.ok if vd else None,
            "connectivity_ok": cc.ok,
            "connectivity_errors": cc.errors[:5],
        },
    }


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(STATIC_DIR)), name="static")
