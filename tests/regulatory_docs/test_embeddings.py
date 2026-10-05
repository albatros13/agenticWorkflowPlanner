"""The hash embedder is deterministic, correctly sized, and normalized."""
import math

import pytest

from agents.regulatory_docs.config import EmbeddingConfig
from agents.regulatory_docs.embeddings import build_embedder


def test_hash_embedder_deterministic_and_sized():
    embed = build_embedder(EmbeddingConfig(provider="hash", dimension=32))
    v1 = embed(["the minister shall grant a permit"])
    v2 = embed(["the minister shall grant a permit"])
    assert v1 == v2                       # deterministic
    assert len(v1[0]) == 32               # dimension honored
    assert abs(math.sqrt(sum(x * x for x in v1[0])) - 1.0) < 1e-9  # L2-normalized


def test_hash_embedder_handles_empty_text():
    embed = build_embedder(EmbeddingConfig(provider="hash", dimension=8))
    [vec] = embed([""])
    assert len(vec) == 8
    assert abs(math.sqrt(sum(x * x for x in vec)) - 1.0) < 1e-9  # valid unit vector, no NaN


def test_different_text_gives_different_vectors():
    embed = build_embedder(EmbeddingConfig(provider="hash", dimension=64))
    a, b = embed(["residence permit"]), embed(["criminal sanction"])
    assert a != b


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        build_embedder(EmbeddingConfig(provider="does-not-exist"))


def test_zero_dimension_rejected():
    with pytest.raises(ValueError):
        build_embedder(EmbeddingConfig(provider="hash", dimension=0))
