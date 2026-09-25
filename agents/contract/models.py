"""Versioned request/response contract shared by every agent (local or remote).

Kept intentionally small and explicit (REST+JSON). The manifest shape is close to an
A2A "agent card" so migrating to a standard later stays cheap (COORDINATOR_PLAN sec 4).
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class Capability(BaseModel):
    """One intent an agent can serve, with optional JSON Schemas for its payloads."""

    intent: str
    description: str = ""
    input_schema: dict | None = None
    output_schema: dict | None = None


class AgentManifest(BaseModel):
    """Self-description returned from ``GET /agent/manifest`` (discovery)."""

    name: str
    version: str = "0.1.0"
    description: str = ""
    capabilities: list[Capability] = Field(default_factory=list)
    supports_async: bool = False

    def serves(self, intent: str) -> bool:
        return any(c.intent == intent for c in self.capabilities)


class AgentRequest(BaseModel):
    """A unit of work handed to an agent."""

    intent: str
    project_id: str | None = None
    correlation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentResult(BaseModel):
    """The outcome of an :class:`AgentRequest`."""

    agent: str
    intent: str
    status: JobStatus = JobStatus.succeeded
    output: dict[str, Any] = Field(default_factory=dict)
    artifacts_ref: str | None = None  # e.g. a run_id under runs/
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == JobStatus.succeeded


class Job(BaseModel):
    """Async job handle for long-running remote invocations."""

    job_id: str
    status: JobStatus = JobStatus.queued
    result: AgentResult | None = None
    error: str | None = None
