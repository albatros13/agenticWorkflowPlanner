"""The regulatory-docs agent handler.

Serves three intents behind the shared agent contract:

* ``upload_document``   — register a regulatory document (raw text or a file path) in the library;
* ``find_documents``    — list/search the library and surface vectorization options from config;
* ``vectorize_document``— chunk + embed a *selected* document and upsert it into a Qdrant cluster,
  using a vectorization profile from ``config/vectorization.json``.

Embedding/Qdrant wiring is injected (``embedder_factory`` / ``store_factory``) so the agent is
testable with a deterministic embedder and an in-memory store, and so provider/cluster choices
stay in config rather than in code.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from agents.contract import AgentHandler, AgentManifest, AgentRequest, AgentResult, Capability
from agents.contract.models import JobStatus
from agents.storage import agent_storage

from .config import EmbeddingConfig, QdrantConfig, VectorizationConfig
from .embeddings import Embedder, build_embedder
from .library import DocumentLibrary
from .qdrant_store import VectorStore, build_store
from .vectorizer import vectorize_document

AGENT_NAME = "regulatory_docs"
UPLOAD_DOCUMENT = "upload_document"
FIND_DOCUMENTS = "find_documents"
VECTORIZE_DOCUMENT = "vectorize_document"

EmbedderFactory = Callable[[EmbeddingConfig], Embedder]
StoreFactory = Callable[[QdrantConfig], VectorStore]


class RegulatoryDocsHandler(AgentHandler):
    def __init__(
        self,
        *,
        library: DocumentLibrary | None = None,
        config: VectorizationConfig | None = None,
        embedder_factory: EmbedderFactory | None = None,
        store_factory: StoreFactory | None = None,
    ):
        self._library = library
        self._config = config
        self._embedder_factory = embedder_factory or build_embedder
        self._store_factory = store_factory or build_store

    # ---- contract ------------------------------------------------------------
    def manifest(self) -> AgentManifest:
        return AgentManifest(
            name=AGENT_NAME,
            version="0.1.0",
            description="Upload/find regulatory documents and vectorize a selected one into Qdrant.",
            supports_async=False,
            capabilities=[
                Capability(
                    intent=UPLOAD_DOCUMENT,
                    description="Register a regulatory document from raw text or a file path.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "path": {"type": "string"},
                            "title": {"type": "string"},
                            "source_document": {"type": "string"},
                        },
                        "anyOf": [{"required": ["text"]}, {"required": ["path"]}],
                    },
                ),
                Capability(
                    intent=FIND_DOCUMENTS,
                    description="List or search uploaded documents; returns vectorization options.",
                    input_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                ),
                Capability(
                    intent=VECTORIZE_DOCUMENT,
                    description="Vectorize a selected document into Qdrant using a config profile.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "doc_id": {"type": "string"},
                            "profile": {"type": "string"},
                        },
                        "required": ["doc_id"],
                    },
                ),
            ],
        )

    def handle(self, request: AgentRequest) -> AgentResult:
        try:
            if request.intent == UPLOAD_DOCUMENT:
                return self._upload(request)
            if request.intent == FIND_DOCUMENTS:
                return self._find(request)
            if request.intent == VECTORIZE_DOCUMENT:
                return self._vectorize(request)
        except Exception as exc:  # surface library/embedding/Qdrant failures uniformly
            return self._fail(request.intent, f"{type(exc).__name__}: {exc}")
        return self._fail(
            request.intent,
            f"Unsupported intent {request.intent!r}; this agent serves "
            f"{UPLOAD_DOCUMENT!r}, {FIND_DOCUMENTS!r}, {VECTORIZE_DOCUMENT!r}.",
        )

    # ---- intents -------------------------------------------------------------
    def _upload(self, request: AgentRequest) -> AgentResult:
        payload = request.payload
        text = payload.get("text")
        path = payload.get("path")
        if path:
            record = self._lib().add_from_path(path)
        elif text and text.strip():
            record = self._lib().add_from_text(
                text,
                title=payload.get("title"),
                source_document=payload.get("source_document"),
            )
        else:
            return self._fail(request.intent, "payload must provide non-empty 'text' or a 'path'.")
        return AgentResult(
            agent=AGENT_NAME,
            intent=request.intent,
            status=JobStatus.succeeded,
            output={"document": record.model_dump(), "profiles": self._cfg().summaries()},
            artifacts_ref=record.doc_id,
        )

    def _find(self, request: AgentRequest) -> AgentResult:
        records = self._lib().find(request.payload.get("query"))
        return AgentResult(
            agent=AGENT_NAME,
            intent=request.intent,
            status=JobStatus.succeeded,
            output={
                "documents": [r.model_dump() for r in records],
                "count": len(records),
                "profiles": self._cfg().summaries(),
            },
        )

    def _vectorize(self, request: AgentRequest) -> AgentResult:
        doc_id = (request.payload.get("doc_id") or "").strip()
        if not doc_id:
            return self._fail(request.intent, "payload.doc_id is required.")
        library = self._lib()
        document = library.load_document(doc_id)  # KeyError -> handled as failure

        profile_name = request.payload.get("profile") or self._cfg().default_profile
        profile = self._cfg().profile(profile_name)

        embedder = self._embedder_factory(profile.embedding)
        store = self._store_factory(profile.qdrant)
        result = vectorize_document(
            document, profile, profile_name=profile_name, embedder=embedder, store=store
        )
        record = library.record_vectorization(
            doc_id,
            {
                "profile": profile_name,
                "collection": result.collection,
                "cluster": result.cluster,
                "dimension": result.dimension,
                "points": result.points_upserted,
                "embedding_model": result.embedding_model,
            },
        )
        return AgentResult(
            agent=AGENT_NAME,
            intent=request.intent,
            status=JobStatus.succeeded,
            output={"vectorization": result.model_dump(), "document": record.model_dump()},
            artifacts_ref=doc_id,
        )

    # ---- helpers -------------------------------------------------------------
    def _lib(self) -> DocumentLibrary:
        if self._library is None:
            root = (
                Path(os.environ["REGDOCS_DIR"])
                if os.getenv("REGDOCS_DIR")
                else agent_storage(AGENT_NAME).path("documents")
            )
            self._library = DocumentLibrary(root)
        return self._library

    def _cfg(self) -> VectorizationConfig:
        if self._config is None:
            self._config = VectorizationConfig.load()
        return self._config

    @staticmethod
    def _fail(intent: str, error: str) -> AgentResult:
        return AgentResult(
            agent=AGENT_NAME, intent=intent, status=JobStatus.failed, error=error
        )
