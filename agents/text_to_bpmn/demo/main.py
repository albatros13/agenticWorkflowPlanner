"""Demo API: natural-language process text -> extracted resources + BPMN diagram.

POST /api/generate runs the pipeline (ingest -> evidence -> IR -> terminology ->
BPMN/DMN) on submitted text and returns the BPMN XML plus the extracted resources
for display. The static frontend at ``/`` renders the diagram with bpmn-js.

GET /api/runs lists the runs saved in the agent's storage, and GET /api/runs/{run_id} returns a
stored run in the same shape as /api/generate (see ``agents/text_to_bpmn/runs.py``), so existing
diagrams can be viewed without generating again.

This endpoint is now a thin back-compat shim over the ``text_to_bpmn`` agent
(``agents/text_to_bpmn``): it selects the provider and delegates to
:class:`~agents.text_to_bpmn.agent.TextToBpmnHandler`, which runs the unchanged pipeline.
New clients should talk to the coordinator instead (see ``todo/COORDINATOR_PLAN.md``).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agents.contract import AgentRequest
from agents.text_to_bpmn.agent import ENGINEER_PROCESS, TextToBpmnHandler
from agents.text_to_bpmn.runs import RunNotFoundError, list_runs, load_run, runs_dir
from agents.text_to_bpmn.pipeline.config import Settings
from agents.text_to_bpmn.pipeline.llm.base import LLMError, LLMProvider
from agents.text_to_bpmn.shaping import select_provider

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Guideline-to-BPMN demo")


class GenerateRequest(BaseModel):
    text: str = Field(min_length=1)
    process_name: str = ""


def _select_provider(settings: Settings) -> LLMProvider:
    """Pick a real provider for the demo (overridable in tests via monkeypatch)."""
    return select_provider(settings)


@app.post("/api/generate")
def generate(req: GenerateRequest) -> dict:
    settings = Settings.from_env()
    try:
        provider = _select_provider(settings)
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    handler = TextToBpmnHandler(provider=provider, settings=settings)
    result = handler.handle(
        AgentRequest(
            intent=ENGINEER_PROCESS,
            payload={"text": req.text, "process_name": req.process_name},
        )
    )
    if not result.ok:  # surface pipeline/LLM failures to the UI
        raise HTTPException(status_code=422, detail=result.error)
    return result.output


@app.get("/api/runs")
def runs() -> dict:
    """Stored runs that have a BPMN model, newest first."""
    return {"runs_dir": str(runs_dir()), "runs": list_runs()}


@app.get("/api/runs/{run_id}")
def run(run_id: str) -> dict:
    """A stored run, in the same shape as /api/generate (plus source_text, saved_at, warnings)."""
    try:
        return load_run(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    # no-cache: browsers revalidate (cheap 304) instead of showing a stale page after an update
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=str(STATIC_DIR)), name="static")
