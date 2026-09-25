"""Coordinator HTTP service — the single natural-language entry point for the user.

    uvicorn agents.coordinator.service:app --port 8000

Endpoints:
* ``POST /coordinator/message``          — main NL entry point
* ``GET  /coordinator/projects``         — list a user's projects
* ``GET  /coordinator/projects/{id}``    — project detail (state, runs, history)
* ``GET  /health``
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .coordinator import Coordinator, CoordinatorResponse
from .factory import build_coordinator
from .projects.store import ProjectNotFoundError

app = FastAPI(title="Coordinator agent")
_coordinator: Coordinator = build_coordinator()

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Single-user default until real auth is added (COORDINATOR_PLAN open question 3).
DEFAULT_USER = os.getenv("DEFAULT_USER_ID", "default")


class MessageRequest(BaseModel):
    text: str = Field(min_length=1)
    user_id: str | None = None
    project_id: str | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/coordinator/message")
def message(req: MessageRequest) -> CoordinatorResponse:
    user_id = req.user_id or DEFAULT_USER
    return _coordinator.handle_message(user_id, req.text, project_id=req.project_id)


@app.get("/coordinator/projects")
def list_projects(user_id: str | None = None) -> dict:
    refs = _coordinator.store.list(user_id or DEFAULT_USER)
    return {"projects": [r.model_dump() for r in refs]}


@app.get("/coordinator/projects/{project_id}")
def get_project(project_id: str, user_id: str | None = None) -> dict:
    try:
        project = _coordinator.store.get(user_id or DEFAULT_USER, project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return project.model_dump(mode="json")


@app.delete("/coordinator/projects/{project_id}")
def delete_project(project_id: str, user_id: str | None = None) -> dict:
    uid = user_id or DEFAULT_USER
    try:
        project = _coordinator.store.get(uid, project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    # deleteProject is allowed from any state; go through the machine to stay consistent.
    _coordinator.machine.apply(project.state, "deleteProject")
    _coordinator.store.delete(uid, project_id)
    _coordinator.cache.remove(project_id)
    return {"deleted": project_id}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main() -> None:  # pragma: no cover - manual entrypoint
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("COORDINATOR_PORT", "8000")))


if __name__ == "__main__":  # pragma: no cover
    main()
