"""Semantic project cache: interface + a wired-in no-op default (implementation deferred).

Lets the coordinator ask "is there an existing/similar project?" before starting a new one.
The embedding/search itself is deferred (COORDINATOR_PLAN sec 8); only the seam ships now.
"""
from .base import SemanticProjectCache, SimilarProject
from .noop import NoOpSemanticCache

__all__ = ["SemanticProjectCache", "SimilarProject", "NoOpSemanticCache", "build_cache"]


def build_cache(kind: str = "noop", **kwargs) -> SemanticProjectCache:
    """Factory selected by the ``SEMANTIC_CACHE`` setting (``noop`` | ``qdrant``)."""
    kind = (kind or "noop").lower()
    if kind == "noop":
        return NoOpSemanticCache()
    if kind == "qdrant":
        from .qdrant import QdrantSemanticCache  # lazy: optional dep + deferred impl

        return QdrantSemanticCache(**kwargs)
    raise ValueError(f"Unknown semantic cache kind: {kind!r}")
