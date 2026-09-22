"""Evidence retrieval interface.

Retrieval must return the *original* evidence text and its location so nothing is
answered from model memory (guideline Step 2). Implementations index
:class:`~src.ingestion.chunker.Chunk` objects and return them with a relevance
score, unchanged.
"""
from __future__ import annotations

import abc

from pydantic import BaseModel

from ..ingestion.base import Location
from ..ingestion.chunker import Chunk


class RetrievedChunk(BaseModel):
    chunk: Chunk
    score: float

    @property
    def text(self) -> str:
        return self.chunk.text

    @property
    def location(self) -> Location:
        return self.chunk.location


class EvidenceIndex(abc.ABC):
    @abc.abstractmethod
    def index(self, chunks: list[Chunk]) -> None: ...

    @abc.abstractmethod
    def search(self, query: str, k: int = 5) -> list[RetrievedChunk]: ...

    def all_chunks(self) -> list[Chunk]:  # pragma: no cover - overridden as needed
        raise NotImplementedError
