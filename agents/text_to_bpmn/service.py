"""Standalone HTTP service exposing the text->BPMN agent via the REST contract.

Run embedded (the coordinator imports :class:`TextToBpmnHandler` directly) or as its own
process::

    uvicorn agents.text_to_bpmn.service:app --port 8120

Then flip ``config/agents.json`` -> ``transport: http, base_url: http://localhost:8120`` and
the coordinator reaches it over the network with no code change.
"""
from __future__ import annotations

import os

from agents.contract.service import make_agent_app

from .agent import TextToBpmnHandler

_handler = TextToBpmnHandler()
app = make_agent_app("text_to_bpmn agent", lambda: _handler)  # shared contract endpoints


def main() -> None:  # pragma: no cover - manual entrypoint
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("TEXT_TO_BPMN_PORT", "8120")))


if __name__ == "__main__":  # pragma: no cover
    main()
