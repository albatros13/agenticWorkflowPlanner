"""Shared agent contract: schemas, transport adapters, and registry.

Everything here is transport-agnostic. The same :class:`~agents.contract.models.AgentRequest`
/ :class:`~agents.contract.models.AgentResult` cross the boundary whether a sub-agent is called
in-process or over HTTP.
"""
from .base import Agent, AgentHandler, HttpAgent, InProcessAgent
from .models import (
    AgentManifest,
    AgentRequest,
    AgentResult,
    Capability,
    Job,
    JobStatus,
)
from .registry import AgentRegistry

__all__ = [
    "Agent",
    "AgentHandler",
    "HttpAgent",
    "InProcessAgent",
    "AgentManifest",
    "AgentRequest",
    "AgentResult",
    "Capability",
    "Job",
    "JobStatus",
    "AgentRegistry",
]
