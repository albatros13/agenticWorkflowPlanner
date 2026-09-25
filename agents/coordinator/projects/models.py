"""Project domain model.

A ``Project`` is the unit the coordinator manages across sessions. It *references* pipeline
runs (which stay under ``runs/``) rather than duplicating artifacts, and carries the current
lifecycle ``state`` (an id from ``config/project-states.json``) plus an audit ``history``.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class HistoryEntry(BaseModel):
    """One coordinator action recorded on the project's audit trail."""

    ts: datetime = Field(default_factory=_now)
    intent: str
    action: str | None = None
    agent: str | None = None
    from_state: str | None = None
    to_state: str | None = None
    summary: str = ""


class ProjectRef(BaseModel):
    """Lightweight list item (avoids loading full projects for listings/search)."""

    id: str
    name: str
    state: str
    updated_at: datetime


class Project(BaseModel):
    id: str
    user_id: str
    name: str
    description: str = ""
    state: str  # current lifecycle state id
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    runs: list[str] = Field(default_factory=list)  # run_ids produced by sub-agents
    history: list[HistoryEntry] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    def ref(self) -> ProjectRef:
        return ProjectRef(id=self.id, name=self.name, state=self.state, updated_at=self.updated_at)

    def record(self, entry: HistoryEntry) -> None:
        self.history.append(entry)
        self.updated_at = _now()
