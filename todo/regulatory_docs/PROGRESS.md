# Regulatory Docs Agent — PROGRESS

The `regulatory_docs` agent lets a user **upload** regulatory documents, **find/select** one from a
library, and **vectorize** the selected document into a **Qdrant** collection for retrieval. It ships
behind the shared agent contract (`agents/contract/`), so the coordinator can route to it in-process
or over HTTP, and it has a standalone demo app.

**Status: implemented and tested against the live cloud Qdrant.** `pytest tests/regulatory_docs -q`
→ **32 passed**, including 2 integration tests that write to and re-read the real cloud cluster
(auto-skipped when credentials / `qdrant-client` are absent).

> **Cloud wiring update (2026-10-05):** the `main`/`kg` clusters now connect to **cloud Qdrant** by
> reading `QDRANT_URL`/`QDRANT_API_KEY` (and `QDRANT_URL_KG`/`QDRANT_API_KEY_KG`) **directly from
> `.env`**, seeded via the repo's dependency-free `load_dotenv`. This removed the previous
> indirection through `api/qdrant_remote_client.py`, which was broken for this agent (it imports the
> third-party `dotenv` package — not a declared dependency — and has import-time side effects).

---

## What was built

### 1. Document library (`library.py`)
- File-backed catalogue under a per-agent storage root. Each document is stored as
  `<root>/<doc_id>/document.json` (parsed `Document` with structure + exact source locations) and
  `meta.json` (`DocumentRecord` the UI lists/selects from, incl. vectorization history).
- `doc_id` is a **content hash**, so re-uploading the same bytes is **idempotent** (returns the
  existing record, no duplicates).
- Reuses the text→BPMN ingestion loaders (`MarkdownLoader`, `PdfLoader`) so markdown / txt / PDF are
  all supported. `find(query)` does a simple substring match over title/source/preview/id.
- `record_vectorization()` appends a per-collection entry and flips `vectorized=True`.

### 2. Embedding backends (`embeddings.py`)
- An `Embedder` maps texts → vectors, selected by the profile's `EmbeddingConfig` via a factory:
  - **`openai`** — real embeddings (lazy import; needs `OPENAI_API_KEY` + the `llm` extra);
    `dimension` is forwarded to `text-embedding-3-*`.
  - **`hash`** — a deterministic, dependency-free local embedder (L2-normalized bag-of-hashed-tokens)
    for offline dev/tests. Not semantically meaningful.

### 3. Qdrant vector store (`qdrant_store.py`)
- `VectorStore` protocol (ensure/upsert/count) so the vectorizer is injectable and tests can use an
  in-memory fake with no optional deps.
- `QdrantDocumentStore` — concrete store over a live `qdrant-client`; lazy imports keep the core
  package installable without the `retrieval` extra.
- `build_qdrant_client(cfg)` resolves the client by cluster:
  - **`main` / `kg`** → **cloud** connection from `.env` credentials (per-cluster env var names);
    raises a clear error if the URL/key are missing.
  - **`memory`** → qdrant-client's in-process mode (dev/tests).
- `ensure_collection()` uses the non-deprecated `collection_exists` + `create_collection` /
  `delete_collection` path and maps config distance names onto the `Distance` enum.

### 4. Vectorizer (`vectorizer.py`)
- Pure orchestration: semantic/section-aware chunking (`SemanticChunker`) → embed → ensure
  collection → upsert one point per chunk. Payload carries **provenance** (doc id, chunk id, source
  document, section, text, page, line range).
- Point ids are a stable `uuid5(doc_id, chunk_id)`, so **re-vectorizing overwrites rather than
  duplicates** (idempotent).
- Returns a `VectorizationResult` (profile, provider/model, dimension, cluster, collection, chunks,
  points upserted).

### 5. Configuration (`config.py` + `config/vectorization.json`)
- Vectorization is driven by **profiles** in `config/vectorization.json` (override location via
  `VECTORIZATION_CONFIG`). A profile names the embedding backend (provider/model/dimension),
  chunking, and the target Qdrant cluster/collection/distance.
- Shipped profiles: **`default`** (OpenAI small → cloud `main`), **`openai-large`** (3072-d →
  cloud `main`), **`offline`** (hash → in-process `memory`, for dev/tests).
- Secrets (`QDRANT_URL`/`QDRANT_API_KEY`, `OPENAI_API_KEY`) stay in `.env`, never in config.
  Swapping a model or collection is a config edit, not a code change.

### 6. Agent handler (`agent.py`)
- `RegulatoryDocsHandler` serves three intents behind the contract:
  - **`upload_document`** — register from raw `text` or a file `path`; returns the record +
    vectorization options.
  - **`find_documents`** — list/search the library; returns documents + options.
  - **`vectorize_document`** — vectorize a selected `doc_id` using a config `profile`.
- Embedding/Qdrant wiring is **injected** (`embedder_factory` / `store_factory`) so the agent is
  testable with a deterministic embedder + in-memory store, and provider/cluster choices live in
  config. All errors surface uniformly as a failed `AgentResult`.
- Registered in `config/agents.json` so `AgentRegistry` maps the three intents to this agent.

### 7. Demo app (`demo/main.py` + `demo/static/index.html`)
- FastAPI shim over the handler: `POST /api/upload`, `POST /api/upload_file` (markdown/txt/PDF),
  `GET /api/documents`, `GET /api/profiles`, `POST /api/vectorize`, `GET /api/health`, plus the
  single-page UI at `/`. Mirrors the text→BPMN / text→eFLINT demos.
- Run: `uvicorn agents.regulatory_docs.demo.main:app --port 8100`.

---

## Tests (`tests/regulatory_docs/`, 32 total)
- **Offline** (hash embedder + in-memory store, no keys/network): `test_library.py`,
  `test_embeddings.py`, `test_vectorizer.py`, `test_config.py`, `test_agent.py` (full
  upload→find→vectorize flow + registry wiring), `test_demo.py` (over HTTP via `TestClient`).
- **Live cloud integration** (`test_cloud_qdrant.py`, new): exercises the agent's **default wiring**
  (`build_store` → live `main` cluster from `.env`) with a deterministic hash embedder, then re-reads
  the cluster independently to prove the data really landed —
  - `test_vectorize_writes_to_cloud_qdrant` — collection exists, `count()` == points upserted, and
    payload/provenance round-trips;
  - `test_revectorize_is_idempotent_in_cloud` — stable point ids overwrite, never duplicate.
  - Writes to a uniquely-named throwaway collection that is deleted afterward; **skips cleanly** when
    `qdrant-client` or `QDRANT_URL`/`QDRANT_API_KEY` are absent.

> The integration test surfaced a **latent bug** in `ensure_collection` (the `Distance` enum was
> indexed by value `"Cosine"` instead of member name `"COSINE"`, raising `KeyError` on any real
> upsert) — never caught before because every prior test used the in-memory fake. Now fixed.

---

## Dependency / config changes
- `pyproject.toml`: pinned `qdrant-client>=1.9,<1.16` to stay within the cloud server's (1.15.x)
  compatibility window; added `python-multipart` to the `dev` extra (the demo file-upload route).
- `config/vectorization.json` + `config.py`: comments updated to describe the direct-`.env` cloud
  wiring instead of `api/qdrant_remote_client.py`.

---

## Deferred / next
- A `search`/retrieval intent (query a collection) to close the loop with the coordinator's
  semantic-cache and verification agents.
- Verify large-document chunking/batching behavior against OpenAI embeddings end-to-end (currently
  covered only offline + via the hash embedder in cloud tests).
- Confirm `.env` is gitignored and rotate any keys that may have been exposed in the repo.
