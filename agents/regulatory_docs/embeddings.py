"""Embedding backends, selected by a profile's :class:`EmbeddingConfig`.

An ``Embedder`` maps a list of texts to a list of vectors. Two backends ship:

* ``openai`` — real embeddings via the OpenAI API (lazy import; needs ``OPENAI_API_KEY`` and the
  ``llm`` extra). ``dimension`` is forwarded to ``text-embedding-3-*`` models, which support it.
* ``hash`` — a deterministic, dependency-free local embedder. The same text always yields the
  same (L2-normalized) vector, so tests/dev run offline without keys or network. It is NOT
  semantically meaningful and must not be used for real retrieval.

Keeping the backend behind a factory (as the retrieval pipeline does with its injected embedder)
keeps the model choice in config and out of the vectorizer / Qdrant code.
"""
from __future__ import annotations

import hashlib
import math
import os
from typing import Callable

from agents.text_to_bpmn.pipeline.config import load_dotenv

from .config import EmbeddingConfig

Embedder = Callable[[list[str]], list[list[float]]]


def build_embedder(cfg: EmbeddingConfig) -> Embedder:
    provider = (cfg.provider or "hash").lower()
    if provider == "hash":
        return _hash_embedder(cfg.dimension)
    if provider == "openai":
        return _openai_embedder(cfg)
    raise ValueError(
        f"Unknown embedding provider {cfg.provider!r} (expected 'openai' or 'hash')."
    )


def _hash_embedder(dimension: int) -> Embedder:
    if dimension <= 0:
        raise ValueError("embedding dimension must be a positive integer")

    def embed(texts: list[str]) -> list[list[float]]:
        return [_hash_vector(t, dimension) for t in texts]

    return embed


def _hash_vector(text: str, dimension: int) -> list[float]:
    """A signed bag-of-hashed-tokens vector, L2-normalized. Deterministic across runs."""
    vec = [0.0] * dimension
    for token in text.lower().split():
        digest = hashlib.sha1(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % dimension
        vec[idx] += 1.0 if digest[4] & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:  # empty/whitespace chunk — return a valid unit vector
        vec[0] = 1.0
        return vec
    return [v / norm for v in vec]


def _openai_embedder(cfg: EmbeddingConfig) -> Embedder:
    try:
        from openai import OpenAI  # noqa: PLC0415 - optional dep
    except ImportError as exc:  # pragma: no cover - optional dep
        raise RuntimeError(
            "OpenAI embeddings require the 'openai' package (`pip install .[llm]`)."
        ) from exc
    load_dotenv()  # seed OPENAI_API_KEY from a local .env (as qdrant_store does for QDRANT_*)
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set; cannot create OpenAI embeddings.")
    client = OpenAI(api_key=api_key)
    # Only text-embedding-3-* accept an explicit output dimension.
    supports_dim = cfg.model.startswith("text-embedding-3")
    batch_size = max(1, cfg.batch_size)

    def embed(texts: list[str]) -> list[list[float]]:  # pragma: no cover - needs key/network
        out: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            kwargs = {"model": cfg.model, "input": batch}
            if supports_dim:
                kwargs["dimensions"] = cfg.dimension
            resp = client.embeddings.create(**kwargs)
            out.extend(item.embedding for item in resp.data)
        return out

    return embed
