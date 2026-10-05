"""Shared fakes for the regulatory_docs tests — no optional deps (qdrant/openai) needed."""
from __future__ import annotations

from agents.regulatory_docs.embeddings import Embedder, build_embedder
from agents.regulatory_docs.config import EmbeddingConfig, QdrantConfig


class InMemoryStore:
    """A VectorStore that keeps points in a dict — stands in for Qdrant in tests."""

    def __init__(self):
        self.dimension: int | None = None
        self.distance: str | None = None
        self.recreated = 0
        self.points: dict[str, dict] = {}

    def ensure_collection(self, *, dimension: int, distance: str, recreate: bool) -> None:
        if recreate or self.dimension is None:
            self.recreated += 1
            self.points = {}
        self.dimension = dimension
        self.distance = distance

    def upsert(self, points: list[dict]) -> int:
        for p in points:
            self.points[p["id"]] = p  # same id overwrites -> idempotent re-vectorize
        return len(points)

    def count(self) -> int:
        return len(self.points)


def offline_embedder_factory(cfg: EmbeddingConfig) -> Embedder:
    """Force the deterministic hash embedder regardless of the profile's provider."""
    return build_embedder(EmbeddingConfig(provider="hash", dimension=cfg.dimension or 64))


def store_factory_for(store: InMemoryStore):
    def factory(cfg: QdrantConfig):
        return store

    return factory
