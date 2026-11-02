"""Standalone HTTP service for the Petri net generator agent.

    uvicorn agents.petri_net_generator.service:app --port 8200

Serves the shared agent contract (``/agent/manifest``, ``/agent/invoke_sync``, ``/agent/invoke``,
``/agent/jobs/{id}``) plus a typed convenience endpoint ``POST /generate``. Point the coordinator
at it with ``transport: http, base_url: http://localhost:8200`` in ``config/agents.json``.
"""
from __future__ import annotations

import os
import uuid

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from agents.contract import AgentRequest, AgentResult
from agents.contract.models import Job, JobStatus

from .agent import GENERATE_PETRI_NET, PetriNetGeneratorHandler
from .schemas import GenerateRequest, GenerateResponse

app = FastAPI(title="petri_net_generator agent")
_handler = PetriNetGeneratorHandler()
_jobs: dict[str, Job] = {}  # in-memory job table (artifacts persist under runs/)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/agent/manifest")
def manifest() -> dict:
    return _handler.manifest().model_dump()


@app.post("/agent/invoke_sync")
def invoke_sync(request: AgentRequest) -> AgentResult:
    return _handler.handle(request)


@app.post("/agent/invoke")
def invoke(request: AgentRequest) -> Job:
    job_id = uuid.uuid4().hex[:12]
    result = _handler.handle(request)
    status = JobStatus.succeeded if result.ok else JobStatus.failed
    job = Job(job_id=job_id, status=status, result=result, error=result.error)
    _jobs[job_id] = job
    return job


@app.get("/agent/jobs/{job_id}")
def job_status(job_id: str) -> Job:
    return _jobs.get(job_id, Job(job_id=job_id, status=JobStatus.failed, error="unknown job"))


@app.post("/generate", response_model=GenerateResponse)
def generate(body: GenerateRequest):
    """Typed endpoint. 200 for ``succeeded`` / ``needs_clarification``, 422 for ``failed``."""
    result = _handler.handle(AgentRequest(intent=GENERATE_PETRI_NET, payload=body.model_dump(mode="json"),
                                          correlation_id=uuid.uuid4().hex[:12]))
    if not result.output:  # internal error: no typed response to return
        return JSONResponse(status_code=500, content={"detail": result.error})
    return JSONResponse(status_code=200 if result.ok else 422, content=result.output)


def main() -> None:  # pragma: no cover - manual entrypoint
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PETRI_NET_PORT", "8200")))


if __name__ == "__main__":  # pragma: no cover
    main()
