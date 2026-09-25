"""Agent protocol + the two transports the coordinator uses to reach a sub-agent.

``InProcessAgent`` wraps an :class:`AgentHandler` (the agent's business logic) and calls it
directly. ``HttpAgent`` speaks the REST contract to a remote agent service. Both expose the
same :class:`Agent` surface, so the coordinator is transport-agnostic (COORDINATOR_PLAN sec 4).
"""
from __future__ import annotations

import abc

from .models import AgentManifest, AgentRequest, AgentResult, JobStatus


class AgentError(RuntimeError):
    """Raised when an agent cannot be reached or returns an unusable response."""


class AgentHandler(abc.ABC):
    """Business logic of an in-process agent (no transport concerns)."""

    @abc.abstractmethod
    def manifest(self) -> AgentManifest: ...

    @abc.abstractmethod
    def handle(self, request: AgentRequest) -> AgentResult: ...


class Agent(abc.ABC):
    """A handle the coordinator invokes; hides whether the agent is local or remote."""

    name: str

    @abc.abstractmethod
    def manifest(self) -> AgentManifest: ...

    @abc.abstractmethod
    def invoke(self, request: AgentRequest) -> AgentResult: ...


class InProcessAgent(Agent):
    """Adapter around an :class:`AgentHandler` living in the same process."""

    def __init__(self, handler: AgentHandler):
        self._handler = handler
        self.name = handler.manifest().name

    def manifest(self) -> AgentManifest:
        return self._handler.manifest()

    def invoke(self, request: AgentRequest) -> AgentResult:
        return self._handler.handle(request)


class HttpAgent(Agent):
    """Adapter that calls a remote agent service over the REST contract.

    ``httpx`` is imported lazily so the core package installs without it; enabling the HTTP
    transport is what pulls the dependency in.
    """

    def __init__(self, name: str, base_url: str, *, timeout_s: float = 300.0):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _client(self):
        try:
            import httpx  # noqa: PLC0415 - optional/transport-only dep
        except ImportError as exc:  # pragma: no cover - optional dep
            raise AgentError(
                "HttpAgent requires 'httpx' (pip install httpx). "
                "It ships in the project's dev extra."
            ) from exc
        return httpx.Client(base_url=self.base_url, timeout=self.timeout_s)

    def manifest(self) -> AgentManifest:
        try:
            with self._client() as client:
                resp = client.get("/agent/manifest")
                resp.raise_for_status()
                return AgentManifest.model_validate(resp.json())
        except AgentError:
            raise
        except Exception as exc:  # normalize transport errors
            raise AgentError(f"Failed to fetch manifest from {self.base_url}: {exc}") from exc

    def invoke(self, request: AgentRequest) -> AgentResult:
        """Synchronous convenience path (POST /agent/invoke_sync)."""
        try:
            with self._client() as client:
                resp = client.post("/agent/invoke_sync", json=request.model_dump())
                resp.raise_for_status()
                return AgentResult.model_validate(resp.json())
        except AgentError:
            raise
        except Exception as exc:  # normalize transport errors into an AgentResult
            return AgentResult(
                agent=self.name,
                intent=request.intent,
                status=JobStatus.failed,
                error=f"{type(exc).__name__}: {exc}",
            )
