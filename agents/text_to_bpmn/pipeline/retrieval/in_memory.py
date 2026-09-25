"""Dependency-free lexical index (TF with IDF weighting).

This is the default retriever: it needs no network or embeddings, so the whole
pipeline and its tests run offline. It is good enough to prove the retrieval
contract — return the original passage and its location for a query — and can be
swapped for :class:`~src.retrieval.qdrant_index.QdrantEvidenceIndex` in
production via the same interface.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from ..ingestion.chunker import Chunk
from .base import EvidenceIndex, RetrievedChunk

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class InMemoryEvidenceIndex(EvidenceIndex):
    def __init__(self) -> None:
        self._chunks: list[Chunk] = []
        self._tf: list[Counter] = []
        self._df: Counter = Counter()

    def index(self, chunks: list[Chunk]) -> None:
        self._chunks = list(chunks)
        self._tf = []
        self._df = Counter()
        for chunk in self._chunks:
            tf = Counter(_tokenize(chunk.text))
            self._tf.append(tf)
            for term in tf:
                self._df[term] += 1

    def all_chunks(self) -> list[Chunk]:
        return list(self._chunks)

    def _idf(self, term: str) -> float:
        n = len(self._chunks)
        return math.log((1 + n) / (1 + self._df.get(term, 0))) + 1.0

    def search(self, query: str, k: int = 5) -> list[RetrievedChunk]:
        q_terms = _tokenize(query)
        scored: list[RetrievedChunk] = []
        for chunk, tf in zip(self._chunks, self._tf):
            score = 0.0
            length = sum(tf.values()) or 1
            for term in q_terms:
                if term in tf:
                    score += (tf[term] / length) * self._idf(term)
            if score > 0:
                scored.append(RetrievedChunk(chunk=chunk, score=round(score, 6)))
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:k]
