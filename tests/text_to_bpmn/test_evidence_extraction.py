"""Guideline Steps 2 & 4: extracted evidence is anchored to real source chunks;
unanchored evidence (citing an unknown chunk) is rejected."""
from pathlib import Path

import pytest

from agents.text_to_bpmn.pipeline.evidence.extractor import EvidenceExtractionError, EvidenceExtractor
from agents.text_to_bpmn.pipeline.ingestion.chunker import SemanticChunker
from agents.text_to_bpmn.pipeline.ingestion.markdown_loader import MarkdownLoader
from agents.text_to_bpmn.pipeline.llm.mock import ScriptedLLMProvider

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _doc_and_chunks():
    doc = MarkdownLoader().load(FIXTURES / "smoke_bph.md")
    chunks = SemanticChunker().chunk(doc)
    return doc, chunks


def test_evidence_items_inherit_chunk_location():
    doc, chunks = _doc_and_chunks()
    med_chunk = next(c for c in chunks if "Medical treatment" in c.section)
    provider = ScriptedLLMProvider({
        "evidence_extraction": {
            "items": [
                {"id": "E1", "text": "Medical treatment is finasteride or an alpha-blocker.",
                 "source_chunk_id": med_chunk.id, "confidence": 0.95},
            ]
        }
    })
    evidence = EvidenceExtractor(provider).extract(doc, chunks)
    assert len(evidence.items) == 1
    item = evidence.items[0]
    assert item.source_chunk_id == med_chunk.id
    assert item.section == med_chunk.section
    assert item.location.line_start == med_chunk.location.line_start
    assert evidence.document_hash  # reproducibility anchor


def test_unanchored_evidence_rejected():
    doc, chunks = _doc_and_chunks()
    provider = ScriptedLLMProvider({
        "evidence_extraction": {
            "items": [
                {"id": "E1", "text": "invented fact", "source_chunk_id": "does-not-exist"},
            ]
        }
    })
    with pytest.raises(EvidenceExtractionError):
        EvidenceExtractor(provider).extract(doc, chunks)
