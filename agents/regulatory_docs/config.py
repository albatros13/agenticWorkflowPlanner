"""Vectorization configuration for the regulatory_docs agent.

Vectorization is driven entirely by *profiles* declared in ``config/vectorization.json``
(override the file location with the ``VECTORIZATION_CONFIG`` env var). A profile names an
embedding backend (provider + model + dimension), how the document is chunked, and the target
Qdrant cluster/collection/distance. Keeping these knobs in config — mirroring
``config/storage.json`` — means swapping an embedding model or collection is a config edit, not
a code change. Secrets (QDRANT_URL/QDRANT_API_KEY, OPENAI_API_KEY) stay in ``.env``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

from agents.paths import CONFIG_DIR

VECTORIZATION_CONFIG = Path(
    os.getenv("VECTORIZATION_CONFIG", str(CONFIG_DIR / "vectorization.json"))
)


class EmbeddingConfig(BaseModel):
    """Which embedding backend turns chunk text into vectors."""

    provider: str = "hash"          # "openai" | "hash"
    model: str = "local-hash"
    dimension: int = 256
    batch_size: int = 64


class ChunkingConfig(BaseModel):
    """How the selected document is split before embedding."""

    max_chars: int = 1200


class QdrantConfig(BaseModel):
    """Where vectors are stored. ``cluster`` selects the credentials/transport."""

    cluster: str = "main"           # "main"/"kg" (cloud, QDRANT_* from .env) | "memory" (in-process)
    collection: str = "regulatory_documents"
    distance: str = "cosine"        # cosine | dot | euclid | manhattan
    recreate_collection: bool = False


class VectorizationProfile(BaseModel):
    description: str = ""
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    qdrant: QdrantConfig = Field(default_factory=QdrantConfig)


class VectorizationConfig(BaseModel):
    """The whole ``vectorization.json`` document: named profiles + a default."""

    default_profile: str = "default"
    profiles: dict[str, VectorizationProfile] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "VectorizationConfig":
        path = Path(path) if path else VECTORIZATION_CONFIG
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        data.pop("$comment", None)
        return cls.model_validate(data)

    def profile(self, name: str | None = None) -> VectorizationProfile:
        name = name or self.default_profile
        if name not in self.profiles:
            raise KeyError(
                f"No vectorization profile named {name!r}; available: {self.profile_names()}"
            )
        return self.profiles[name]

    def profile_names(self) -> list[str]:
        return sorted(self.profiles)

    def summaries(self) -> list[dict]:
        """Lightweight profile listing for a UI to render as vectorization options."""
        return [
            {
                "name": name,
                "description": p.description,
                "provider": p.embedding.provider,
                "model": p.embedding.model,
                "dimension": p.embedding.dimension,
                "cluster": p.qdrant.cluster,
                "collection": p.qdrant.collection,
                "is_default": name == self.default_profile,
            }
            for name, p in sorted(self.profiles.items())
        ]
