"""Standalone HTTP service for the eFLINT verifier agent.

    uvicorn agents.eflint_verifier.service:app --port 8300

Serves the shared agent contract (``/agent/manifest``, ``/agent/invoke_sync``, ``/agent/invoke``,
``/agent/jobs/{id}``) plus a typed convenience endpoint ``POST /verify``. The external eFLINT
model-checking service it wraps is configured with ``EFLINT_VERIFIER_URL`` (default
``http://127.0.0.1:8100``). Point the coordinator at this wrapper with
``transport: http, base_url: http://localhost:8300`` in ``config/agents.json``.
"""
from __future__ import annotations

import os
import uuid

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from agents.contract import AgentRequest, AgentResult
from agents.contract.models import Job, JobStatus

from .agent import VERIFY_EFLINT, EflintVerifierHandler
from .schemas import VerifyRequest, VerifyResponse

app = FastAPI(title="eflint_verifier agent")
_handler = EflintVerifierHandler()
_jobs: dict[str, Job] = {}  # in-memory job table (reports persist under runs/)


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
    job = Job(job_id=job_id, status=JobStatus.succeeded if result.ok else JobStatus.failed,
              result=result, error=result.error)
    _jobs[job_id] = job
    return job


@app.get("/agent/jobs/{job_id}")
def job_status(job_id: str) -> Job:
    return _jobs.get(job_id, Job(job_id=job_id, status=JobStatus.failed, error="unknown job"))


@app.post("/verify", response_model=VerifyResponse)
def verify(body: VerifyRequest):
    """Typed endpoint: 200 with the verdict, 502 when the eFLINT service failed or is unreachable."""
    result = _handler.handle(AgentRequest(intent=VERIFY_EFLINT, payload=body.model_dump(mode="json"),
                                          correlation_id=uuid.uuid4().hex[:12]))
    if not result.ok:
        return JSONResponse(status_code=502, content={"detail": result.error, **result.output})
    return JSONResponse(status_code=200, content=result.output)


def main() -> None:  # pragma: no cover - manual entrypoint
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("EFLINT_VERIFIER_PORT", "8300")))


if __name__ == "__main__":  # pragma: no cover
    main()
