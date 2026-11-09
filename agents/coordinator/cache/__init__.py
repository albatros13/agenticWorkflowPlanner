"""Semantic project cache: interface + a wired-in no-op default (implementation deferred).

Lets the coordinator ask "is there an existing/similar project?" before starting a new one.
The embedding/search itself is deferred; only the seam ships now.
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
        # Fail at start-up, not on the first "new project": every method of the skeleton raises.
        raise ValueError("SEMANTIC_CACHE=qdrant is not implemented yet; use 'noop'.")
    raise ValueError(f"Unknown semantic cache kind: {kind!r}")
