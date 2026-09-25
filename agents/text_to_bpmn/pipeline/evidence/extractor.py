"""Pass A: turn retrieved/ingested chunks into source-anchored evidence items.

The LLM only *selects and segments* statements; the extractor deterministically
copies each item's location from the cited chunk. Items that cite an unknown
chunk are rejected — this is what enforces "traceable to source" and blocks
answers from model memory (guideline Steps 2 & 4).
"""
from __future__ import annotations

from ..config import Settings
from ..ingestion.base import Document
from ..ingestion.chunker import Chunk
from ..llm.base import LLMProvider
from ..pipeline.artifacts import ArtifactStore
from ..prompts import EVIDENCE_SYSTEM, EVIDENCE_USER_TEMPLATE, evidence_schema
from .models import EvidenceItem, EvidenceSet


class EvidenceExtractionError(RuntimeError):
    pass


class EvidenceExtractor:
    task = "evidence_extraction"

    def __init__(self, provider: LLMProvider, settings: Settings | None = None):
        self.provider = provider
        self.settings = settings or Settings.from_env()

    def extract(self, document: Document, chunks: list[Chunk]) -> EvidenceSet:
        by_id = {c.id: c for c in chunks}
        rendered = "\n\n".join(
            f"[{c.id}] (section: {c.section or 'n/a'})\n{c.text}" for c in chunks
        )
        prompt = EVIDENCE_USER_TEMPLATE.format(
            source_document=document.source_document, chunks=rendered
        )
        result = self.provider.generate_json(
            task=self.task,
            system=EVIDENCE_SYSTEM,
            prompt=prompt,
            schema=evidence_schema(),
        )

        items: list[EvidenceItem] = []
        for raw in result.data.get("items", []):
            chunk_id = raw.get("source_chunk_id")
            chunk = by_id.get(chunk_id)
            if chunk is None:
                raise EvidenceExtractionError(
                    f"Evidence item {raw.get('id')!r} cites unknown chunk {chunk_id!r}; "
                    "refusing to accept unanchored evidence."
                )
            items.append(
                EvidenceItem(
                    id=raw["id"],
                    text=raw["text"],
                    source_document=document.source_document,
                    section=chunk.section,
                    page=chunk.location.page,
                    location=chunk.location,
                    source_chunk_id=chunk_id,
                    confidence=float(raw.get("confidence", 1.0)),
                )
            )
        if not items:
            raise EvidenceExtractionError("No evidence extracted from document")
        return EvidenceSet(
            document_id=document.id,
            document_hash=ArtifactStore.content_hash(document.text),
            items=items,
        )
