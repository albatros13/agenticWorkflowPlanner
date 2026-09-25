"""Evidence items: atomic, source-anchored statements extracted from a document.

Shape follows guideline Step 2. Every downstream IR element references one or
more of these by ``id``, which is what makes the whole model auditable.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..ingestion.base import Location


class EvidenceItem(BaseModel):
    id: str
    text: str                              # verbatim / faithful source statement
    source_document: str
    section: str = ""
    page: int | None = None
    location: Location = Field(default_factory=Location)
    source_chunk_id: str | None = None     # chunk the statement was taken from
    confidence: float = 1.0


class EvidenceSet(BaseModel):
    document_id: str
    document_hash: str
    items: list[EvidenceItem] = Field(default_factory=list)

    def ids(self) -> set[str]:
        return {i.id for i in self.items}

    def get(self, evidence_id: str) -> EvidenceItem | None:
        return next((i for i in self.items if i.id == evidence_id), None)
