"""The REST contract every agent service exposes, built once.

``make_agent_app`` returns a FastAPI app with the shared endpoints

* ``GET  /health``
* ``GET  /agent/manifest``
* ``POST /agent/invoke_sync`` — blocking invocation
* ``POST /agent/invoke``      — returns a :class:`Job` handle (runs inline: single-worker service)
* ``GET  /agent/jobs/{id}``

so each agent's ``service.py`` only adds its own typed convenience endpoints. ``get_handler`` is
called per request, so a test can replace the module's handler after the app is built.
"""
from __future__ import annotations

import uuid
from collections import OrderedDict
from typing import Callable

from fastapi import FastAPI

from .base import AgentHandler
from .models import AgentRequest, AgentResult, Job, JobStatus

MAX_JOBS = 1000  # finished jobs kept for polling; the oldest are dropped beyond this


class JobTable:
    """Bounded in-memory job table (artifacts persist in the agent's runs folder)."""

    def __init__(self, max_jobs: int = MAX_JOBS):
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._max = max_jobs

    def add(self, job: Job) -> Job:
        self._jobs[job.job_id] = job
        while len(self._jobs) > self._max:
            self._jobs.popitem(last=False)
        return job

    def get(self, job_id: str) -> Job:
        return self._jobs.get(job_id) or Job(job_id=job_id, status=JobStatus.failed, error="unknown job")

    def __len__(self) -> int:
        return len(self._jobs)


def make_agent_app(title: str, get_handler: Callable[[], AgentHandler], *,
                   max_jobs: int = MAX_JOBS) -> FastAPI:
    app = FastAPI(title=title)
    jobs = JobTable(max_jobs)
    app.state.jobs = jobs

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/agent/manifest")
    def manifest() -> dict:
        return get_handler().manifest().model_dump()

    @app.post("/agent/invoke_sync")
    def invoke_sync(request: AgentRequest) -> AgentResult:
        """Blocking invocation — convenient for fast/local callers and tests."""
        return get_handler().handle(request)

    @app.post("/agent/invoke")
    def invoke(request: AgentRequest) -> Job:
        """Returns a job handle so remote clients use the same poll-based flow they would
        against a real queue. Runs inline here (single-worker service)."""
        result = get_handler().handle(request)
        status = JobStatus.succeeded if result.ok else JobStatus.failed
        return jobs.add(Job(job_id=uuid.uuid4().hex[:12], status=status, result=result, error=result.error))

    @app.get("/agent/jobs/{job_id}")
    def job_status(job_id: str) -> Job:
        return jobs.get(job_id)

    return app
