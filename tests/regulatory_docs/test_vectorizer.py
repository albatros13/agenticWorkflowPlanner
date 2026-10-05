"""Vectorizer chunks, embeds, and upserts one deterministic point per chunk."""
import pytest

from agents.regulatory_docs.config import VectorizationProfile
from agents.regulatory_docs.embeddings import build_embedder
from agents.regulatory_docs.library import DocumentLibrary
from agents.regulatory_docs.vectorizer import vectorize_document

from .conftest import InMemoryStore

MD = """# Reg

## A
Alpha clause text here.

## B
Beta clause text here.
"""


def _doc(tmp_path):
    lib = DocumentLibrary(tmp_path)
    rec = lib.add_from_text(MD, title="Reg")
    return lib.load_document(rec.doc_id)


def test_vectorize_upserts_points_with_provenance(tmp_path):
    doc = _doc(tmp_path)
    profile = VectorizationProfile.model_validate(
        {"embedding": {"provider": "hash", "dimension": 16}, "qdrant": {"cluster": "memory", "collection": "regs"}}
    )
    store = InMemoryStore()
    result = vectorize_document(
        doc, profile, profile_name="offline",
        embedder=build_embedder(profile.embedding), store=store,
    )
    assert result.chunks >= 2
    assert result.points_upserted == result.chunks
    assert result.dimension == 16
    assert store.count() == result.chunks
    # Provenance is carried in the payload.
    sample = next(iter(store.points.values()))
    assert sample["payload"]["doc_id"] == doc.id
    assert sample["payload"]["section"]
    assert sample["payload"]["text"]


def test_revectorize_is_idempotent(tmp_path):
    doc = _doc(tmp_path)
    profile = VectorizationProfile.model_validate(
        {"embedding": {"provider": "hash", "dimension": 16}, "qdrant": {"cluster": "memory", "collection": "regs"}}
    )
    store = InMemoryStore()
    embed = build_embedder(profile.embedding)
    r1 = vectorize_document(doc, profile, profile_name="o", embedder=embed, store=store)
    before = store.count()
    r2 = vectorize_document(doc, profile, profile_name="o", embedder=embed, store=store)
    # Same (doc, chunk) ids -> stable point ids -> no duplication.
    assert store.count() == before == r1.chunks == r2.chunks


def test_empty_document_raises(tmp_path):
    from agents.text_to_bpmn.pipeline.ingestion.base import Document

    profile = VectorizationProfile()
    with pytest.raises(ValueError):
        vectorize_document(
            Document(id="x", source_document="x", text="", blocks=[]),
            profile, profile_name="o",
            embedder=build_embedder(profile.embedding), store=InMemoryStore(),
        )
