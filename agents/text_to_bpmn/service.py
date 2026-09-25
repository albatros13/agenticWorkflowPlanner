"""Standalone HTTP service exposing the text->BPMN agent via the REST contract.

Run embedded (the coordinator imports :class:`TextToBpmnHandler` directly) or as its own
process::

    uvicorn agents.text_to_bpmn.service:app --port 8100

Then flip ``config/agents.json`` -> ``transport: http, base_url: http://localhost:8100`` and
the coordinator reaches it over the network with no code change (COORDINATOR_PLAN sec 9-10).
"""
from __future__ import annotations

import os
import uuid

from fastapi import FastAPI

from agents.contract import AgentRequest, AgentResult
from agents.contract.models import Job, JobStatus

from .agent import TextToBpmnHandler

app = FastAPI(title="text_to_bpmn agent")
_handler = TextToBpmnHandler()
_jobs: dict[str, Job] = {}  # in-memory job table (artifacts persist under runs/)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/agent/manifest")
def manifest() -> dict:
    return _handler.manifest().model_dump()


@app.post("/agent/invoke_sync")
def invoke_sync(request: AgentRequest) -> AgentResult:
    """Blocking invocation — convenient for fast/local callers and tests."""
    return _handler.handle(request)


@app.post("/agent/invoke")
def invoke(request: AgentRequest) -> Job:
    """Async invocation. Runs inline here (single-worker demo) but returns a job handle so
    remote clients use the same poll-based flow they would against a real queue."""
    job_id = uuid.uuid4().hex[:12]
    result = _handler.handle(request)
    status = JobStatus.succeeded if result.ok else JobStatus.failed
    job = Job(job_id=job_id, status=status, result=result, error=result.error)
    _jobs[job_id] = job
    return job


@app.get("/agent/jobs/{job_id}")
def job_status(job_id: str) -> Job:
    return _jobs.get(job_id, Job(job_id=job_id, status=JobStatus.failed, error="unknown job"))


def main() -> None:  # pragma: no cover - manual entrypoint
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("TEXT_TO_BPMN_PORT", "8100")))


if __name__ == "__main__":  # pragma: no cover
    main()
