"""Vectorize a selected document into a Qdrant collection.

Pure orchestration: chunk the document (semantic, section-aware), embed the chunks with the
profile's embedder, ensure the target collection exists, and upsert one point per chunk with a
payload that keeps provenance (doc id, section, source location, text). Both the embedder and the
store are injected, so this module has no hard dependency on OpenAI or qdrant-client and is fully
unit-testable.
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel

from agents.text_to_bpmn.pipeline.ingestion.base import Document
from agents.text_to_bpmn.pipeline.ingestion.chunker import SemanticChunker

from .config import VectorizationProfile
from .embeddings import Embedder
from .qdrant_store import VectorStore

# Stable namespace so a (doc_id, chunk_id) pair always maps to the same Qdrant point id —
# re-vectorizing a document overwrites its points instead of duplicating them.
_POINT_NS = uuid.UUID("6f9b2d21-1c2a-4f3e-9a7c-7d5b8e0a1f44")


class VectorizationResult(BaseModel):
    doc_id: str
    profile: str
    embedding_provider: str
    embedding_model: str
    dimension: int
    cluster: str
    collection: str
    chunks: int
    points_upserted: int


def vectorize_document(
    document: Document,
    profile: VectorizationProfile,
    *,
    profile_name: str,
    embedder: Embedder,
    store: VectorStore,
) -> VectorizationResult:
    chunks = SemanticChunker(max_chars=profile.chunking.max_chars).chunk(document)
    if not chunks:
        raise ValueError("Document produced no chunks to vectorize.")

    vectors = embedder([c.text for c in chunks])
    if len(vectors) != len(chunks):
        raise RuntimeError(
            f"Embedder returned {len(vectors)} vectors for {len(chunks)} chunks."
        )
    dimension = len(vectors[0]) if vectors else profile.embedding.dimension

    store.ensure_collection(
        dimension=dimension,
        distance=profile.qdrant.distance,
        recreate=profile.qdrant.recreate_collection,
    )

    points = [
        {
            "id": str(uuid.uuid5(_POINT_NS, f"{document.id}:{chunk.id}")),
            "vector": vector,
            "payload": {
                "doc_id": document.id,
                "chunk_id": chunk.id,
                "source_document": document.source_document,
                "section": chunk.section,
                "text": chunk.text,
                "page": chunk.location.page,
                "line_start": chunk.location.line_start,
                "line_end": chunk.location.line_end,
            },
        }
        for chunk, vector in zip(chunks, vectors)
    ]
    upserted = store.upsert(points)

    return VectorizationResult(
        doc_id=document.id,
        profile=profile_name,
        embedding_provider=profile.embedding.provider,
        embedding_model=profile.embedding.model,
        dimension=dimension,
        cluster=profile.qdrant.cluster,
        collection=profile.qdrant.collection,
        chunks=len(chunks),
        points_upserted=upserted,
    )
