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

from agents.storage import is_safe_id

from .coordinator import Coordinator, CoordinatorResponse
from .factory import build_coordinator
from .projects.store import ProjectNotFoundError

app = FastAPI(title="Coordinator agent")
# Built on first use, not at import: importing the module (tests, tooling) must not construct every
# agent and read every config. Tests may assign a ready-made coordinator here.
_coordinator: Coordinator | None = None

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Single-user default until real auth is added.
DEFAULT_USER = os.getenv("DEFAULT_USER_ID", "default")


def _get() -> Coordinator:
    global _coordinator
    if _coordinator is None:
        _coordinator = build_coordinator()
    return _coordinator


def _user(user_id: str | None) -> str:
    """The caller's user id; 400 when it is not a safe single path component (e.g. ``..``)."""
    uid = user_id or DEFAULT_USER
    if not is_safe_id(uid):
        raise HTTPException(status_code=400, detail=f"invalid user id {uid!r}")
    return uid


class MessageRequest(BaseModel):
    text: str = Field(min_length=1)
    user_id: str | None = None
    project_id: str | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/coordinator/message")
def message(req: MessageRequest) -> CoordinatorResponse:
    return _get().handle_message(_user(req.user_id), req.text, project_id=req.project_id)


@app.get("/coordinator/projects")
def list_projects(user_id: str | None = None) -> dict:
    refs = _get().store.list(_user(user_id))
    return {"projects": [r.model_dump() for r in refs]}


def _project(uid: str, project_id: str):
    try:
        return _get().store.get(uid, project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/coordinator/projects/{project_id}")
def get_project(project_id: str, user_id: str | None = None) -> dict:
    return _project(_user(user_id), project_id).model_dump(mode="json")


@app.delete("/coordinator/projects/{project_id}")
def delete_project(project_id: str, user_id: str | None = None) -> dict:
    _get().delete_project(_project(_user(user_id), project_id))
    return {"deleted": project_id}


@app.get("/")
def index() -> FileResponse:
    # no-cache: browsers revalidate (cheap 304) instead of showing a stale page after an update
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main() -> None:  # pragma: no cover - manual entrypoint
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("COORDINATOR_PORT", "8000")))


if __name__ == "__main__":  # pragma: no cover
    main()
