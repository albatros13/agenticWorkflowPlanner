"""HTTP client for the external eFLINT model-checking webservice (job API, ``/v1/jobs``).

The service is asynchronous: ``POST /v1/jobs`` queues a job, ``GET /v1/jobs/{id}`` reports its
status, ``GET /v1/jobs/{id}/result`` returns the result once it ``succeeded``. :meth:`run` wraps
that into one blocking call, cancels the job if it outlives ``wait_timeout_s``, and deletes the
finished job so the service does not accumulate results.

``httpx`` is imported lazily (like :class:`~agents.contract.base.HttpAgent`). Tests inject an
``httpx.MockTransport`` through ``transport``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from .config import VerifierSettings

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}


class VerifierError(RuntimeError):
    """Base class; ``code`` mirrors the service's ``error.code`` when there is one."""

    def __init__(self, message: str, *, code: str = "verifier_error"):
        super().__init__(message)
        self.code = code


class VerifierUnavailable(VerifierError):
    """The service could not be reached (not running, wrong URL, 503)."""


class VerifierRejected(VerifierError):
    """The service rejected the request itself (HTTP 400: malformed fields/combination)."""


@dataclass
class JobOutcome:
    job_id: str
    status: str                      # succeeded | failed | cancelled | timed_out
    result: dict[str, Any] | None    # only for succeeded jobs
    error: dict[str, Any] | None     # the service's {code, message} for failed jobs
    elapsed_s: float


class EflintVerifierClient:
    def __init__(
        self,
        settings: VerifierSettings | None = None,
        *,
        transport=None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings or VerifierSettings.from_env()
        self._transport = transport
        self._sleep = sleep

    # --- low level -------------------------------------------------------------------------
    def _client(self):
        try:
            import httpx  # noqa: PLC0415 - optional dependency
        except ImportError as exc:  # pragma: no cover - optional dep
            raise VerifierError("The eFLINT verifier wrapper requires 'httpx' (pip install httpx).",
                                code="missing_dependency") from exc
        kwargs = {"base_url": self.settings.base_url.rstrip("/"), "timeout": self.settings.request_timeout_s}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def _call(self, client, method: str, path: str, **kwargs):
        import httpx  # noqa: PLC0415

        try:
            resp = client.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise VerifierUnavailable(
                f"eFLINT verifier service at {self.settings.base_url} is unreachable: {exc}",
                code="unavailable") from exc
        if resp.status_code == 503:
            raise VerifierUnavailable(_error_message(resp), code="unavailable")
        if resp.status_code == 400:
            body = _json(resp).get("error") or {}
            raise VerifierRejected(body.get("message") or resp.text, code=body.get("code", "invalid_request"))
        if resp.status_code >= 400:
            raise VerifierError(f"{method} {path} -> HTTP {resp.status_code}: {_error_message(resp)}",
                                code=f"http_{resp.status_code}")
        return resp

    # --- public ----------------------------------------------------------------------------
    def submit(self, request: dict[str, Any]) -> str:
        with self._client() as client:
            return _json(self._call(client, "POST", "/v1/jobs", json=request))["id"]

    def status(self, job_id: str) -> dict[str, Any]:
        with self._client() as client:
            return _json(self._call(client, "GET", f"/v1/jobs/{job_id}"))

    def run(self, request: dict[str, Any]) -> JobOutcome:
        """Submit ``request``, wait for a terminal status, fetch the result, delete the job."""
        s = self.settings
        started = time.monotonic()
        with self._client() as client:
            job_id = _json(self._call(client, "POST", "/v1/jobs", json=request))["id"]
            delay = s.poll_interval_s
            while True:
                status = _json(self._call(client, "GET", f"/v1/jobs/{job_id}"))
                if status.get("status") in TERMINAL:
                    break
                if time.monotonic() - started > s.wait_timeout_s:
                    self._call(client, "POST", f"/v1/jobs/{job_id}/cancel")
                    status = {"status": "timed_out", "error": {
                        "code": "wait_timeout",
                        "message": f"No result after {s.wait_timeout_s:.0f}s; the job was cancelled. "
                                   "Lower the search bound (max_states) or raise EFLINT_VERIFIER_WAIT_TIMEOUT_S."}}
                    break
                self._sleep(delay)
                delay = min(delay * 2, s.max_poll_interval_s)

            result = None
            if status.get("status") == "succeeded":
                result = _json(self._call(client, "GET", f"/v1/jobs/{job_id}/result"))
            try:  # best effort: a cancelled job may still be shutting down (409)
                self._call(client, "DELETE", f"/v1/jobs/{job_id}")
            except VerifierError:
                pass
        return JobOutcome(job_id=job_id, status=status.get("status", "failed"), result=result,
                          error=status.get("error"), elapsed_s=round(time.monotonic() - started, 3))


def _json(resp) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _error_message(resp) -> str:
    err = _json(resp).get("error")
    if isinstance(err, dict) and err.get("message"):
        return err["message"]
    return resp.text[:500]
