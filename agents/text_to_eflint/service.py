"""Standalone HTTP service for the text->eFLINT agent.

    uvicorn agents.text_to_eflint.service:app --port 8091

Serves the shared agent contract (``/agent/manifest``, ``/agent/invoke_sync``, ``/agent/invoke``,
``/agent/jobs/{id}``) plus convenience endpoints ``POST /generate`` and ``POST /validate`` that
take the intent payload directly. Point the coordinator at it with ``transport: http,
base_url: http://localhost:8091`` in ``config/agents.json``.
"""
from __future__ import annotations

import os
import uuid

from fastapi.responses import JSONResponse

from agents.contract import AgentRequest
from agents.contract.service import make_agent_app

from .agent import GENERATE_EFLINT, VALIDATE_EFLINT, TextToEflintHandler

_handler = TextToEflintHandler()
app = make_agent_app("text_to_eflint agent", lambda: _handler)  # shared contract endpoints


def _direct(intent: str, payload: dict) -> JSONResponse:
    """200 with the output (``output.status`` says succeeded / needs_review), 422 on failure."""
    result = _handler.handle(AgentRequest(intent=intent, payload=payload, correlation_id=uuid.uuid4().hex[:12]))
    if not result.ok:
        return JSONResponse(status_code=422, content={"detail": result.error})
    return JSONResponse(status_code=200, content=result.output)


@app.post("/generate")
def generate(payload: dict) -> JSONResponse:
    return _direct(GENERATE_EFLINT, payload)


@app.post("/validate")
def validate(payload: dict) -> JSONResponse:
    return _direct(VALIDATE_EFLINT, payload)


def main() -> None:  # pragma: no cover - manual entrypoint
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("TEXT_TO_EFLINT_PORT", "8091")))


if __name__ == "__main__":  # pragma: no cover
    main()
