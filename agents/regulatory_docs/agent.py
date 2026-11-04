"""The regulatory-docs agent handler.

Serves three intents behind the shared agent contract:

* ``upload_document``   — register a regulatory document (raw text or a file path) in the library;
* ``find_documents``    — list/search the library and surface vectorization options from config;
* ``vectorize_document``— chunk + embed a *selected* document and upsert it into a Qdrant cluster,
  using a vectorization profile from ``config/vectorization.json``;
* ``list_vectorized``   — list the documents actually stored in the profiles' Qdrant collections
  (Qdrant is the source of truth, so this includes documents absent from the local library);
* ``search_vectors``    — semantic search: embed a query with a profile's model and return the
  nearest chunks from that profile's collection.

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
LIST_VECTORIZED = "list_vectorized"
SEARCH_VECTORS = "search_vectors"
INTENTS = (UPLOAD_DOCUMENT, FIND_DOCUMENTS, VECTORIZE_DOCUMENT, LIST_VECTORIZED, SEARCH_VECTORS)

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
                Capability(
                    intent=LIST_VECTORIZED,
                    description="List documents stored in Qdrant, per profile collection.",
                    input_schema={
                        "type": "object",
                        "properties": {"profile": {"type": "string"}},
                    },
                ),
                Capability(
                    intent=SEARCH_VECTORS,
                    description="Semantic search over a profile's Qdrant collection.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "profile": {"type": "string"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                        },
                        "required": ["query"],
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
            if request.intent == LIST_VECTORIZED:
                return self._list_vectorized(request)
            if request.intent == SEARCH_VECTORS:
                return self._search(request)
        except Exception as exc:  # surface library/embedding/Qdrant failures uniformly
            return self._fail(request.intent, f"{type(exc).__name__}: {exc}")
        return self._fail(
            request.intent,
            f"Unsupported intent {request.intent!r}; this agent serves "
            + ", ".join(repr(i) for i in INTENTS) + ".",
        )

    # ---- intents -------------------------------------------------------------
    def _upload(self, request: AgentRequest) -> AgentResult:
        payload = request.payload
        text = payload.get("text")
        path = payload.get("path")
        if path:
            record = self._lib().add_from_path(path, name=payload.get("title"))
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
            document,
            profile,
            profile_name=profile_name,
            embedder=embedder,
            store=store,
            title=library.get(doc_id).title,
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

    def _list_vectorized(self, request: AgentRequest) -> AgentResult:
        """Documents per (cluster, collection) of the selected profile(s), read from Qdrant.

        Each target is queried independently so one unreachable cluster reports an error for its
        own collection instead of failing the whole listing.
        """
        cfg = self._cfg()
        requested = request.payload.get("profile")
        names = [requested] if requested else cfg.profile_names()
        library_ids = {r.doc_id for r in self._lib().find()}

        targets: dict[tuple[str, str], dict] = {}
        for name in names:
            profile = cfg.profile(name)
            key = (profile.qdrant.cluster, profile.qdrant.collection)
            if key in targets:
                targets[key]["profiles"].append(name)
                continue
            target = {
                "cluster": key[0],
                "collection": key[1],
                "profiles": [name],
                "documents": [],
                "error": None,
            }
            try:
                docs = self._store_factory(profile.qdrant).list_documents()
                target["documents"] = [
                    {**d, "in_library": d["doc_id"] in library_ids} for d in docs
                ]
            except Exception as exc:  # noqa: BLE001 - surface per-collection failures
                target["error"] = f"{type(exc).__name__}: {exc}"
            targets[key] = target

        collections = list(targets.values())
        return AgentResult(
            agent=AGENT_NAME,
            intent=request.intent,
            status=JobStatus.succeeded,
            output={
                "collections": collections,
                "count": sum(len(c["documents"]) for c in collections),
            },
        )

    def _search(self, request: AgentRequest) -> AgentResult:
        query = (request.payload.get("query") or "").strip()
        if not query:
            return self._fail(request.intent, "payload.query is required.")
        profile_name = request.payload.get("profile") or self._cfg().default_profile
        profile = self._cfg().profile(profile_name)
        limit = max(1, min(int(request.payload.get("limit") or 5), 50))

        vector = self._embedder_factory(profile.embedding)([query])[0]
        hits = self._store_factory(profile.qdrant).search(vector, limit)
        library_ids = {r.doc_id for r in self._lib().find()}
        for hit in hits:
            hit["in_library"] = hit.get("doc_id") in library_ids
        return AgentResult(
            agent=AGENT_NAME,
            intent=request.intent,
            status=JobStatus.succeeded,
            output={
                "query": query,
                "profile": profile_name,
                "cluster": profile.qdrant.cluster,
                "collection": profile.qdrant.collection,
                "hits": hits,
            },
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
