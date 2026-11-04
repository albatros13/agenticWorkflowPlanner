# regulatory_docs — document library + vectorization into Qdrant

The `regulatory_docs` agent lets a user **upload** regulatory documents, **find and select** one
from a library, and **vectorize** the selected document into **Qdrant** for later retrieval. The
`text_to_eflint` agent can also read library documents by `document_id`.

- **Intents:** `upload_document`, `find_documents`, `vectorize_document`.
- **Registered:** in-process in `config/agents.json`, but the coordinator's intent classifier does
  not emit these intents yet. Use the demo app, or call the agent directly.

## Intents

| Intent | Payload | Output |
| --- | --- | --- |
| `upload_document` | `text` or `path`; optional `title`, `source_document` | `document` (the library record) and the available `profiles` |
| `find_documents` | optional `query` | `documents`, `count`, `profiles` |
| `vectorize_document` | `doc_id`; optional `profile` (default from config) | `vectorization` (collection, cluster, dimension, points, embedding model) and the updated `document` |

## How it works

- **Document library** (`library.py`): a file-backed catalogue under the agent's private storage
  (`documents/<doc_id>/document.json` + `meta.json`). `doc_id` is a **content hash**, so
  re-uploading the same bytes is idempotent. It reuses the text→BPMN ingestion loaders, so
  Markdown, plain text and PDF are supported. Each record keeps its vectorization history.
- **Vectorization profiles** (`config.py`, `config/vectorization.json`): a named profile fixes
  the embedding backend (provider, model, dimension, batch size), the chunking, and the target
  Qdrant cluster, collection and distance. Swapping a model or collection is a config edit.
  Shipped profiles:
  - **`default`**: OpenAI `text-embedding-3-small`, 1536-d → cloud `main` cluster;
  - **`openai-large`**: `text-embedding-3-large`, 3072-d → cloud `main` cluster;
  - **`offline`**: a deterministic local hash embedder → in-process Qdrant (dev/tests only).
- **Embedders** (`embeddings.py`): `openai` (real embeddings, needs `OPENAI_API_KEY`) or `hash`
  (dependency-free and deterministic, but *not* semantically meaningful).
- **Qdrant store** (`qdrant_store.py`): clusters `main` / `kg` connect to **cloud Qdrant** using
  `QDRANT_URL` / `QDRANT_API_KEY` (or `QDRANT_URL_KG` / `QDRANT_API_KEY_KG`) from `.env`;
  `memory` uses qdrant-client's in-process mode.
- **Vectorizer** (`vectorizer.py`): section-aware chunking → embed → ensure the collection exists →
  upsert one point per chunk, with full **provenance** in the payload (doc id, chunk id, source,
  section, page, line range). Point IDs are a stable `uuid5(doc_id, chunk_id)`, so re-vectorizing
  **overwrites rather than duplicates**.
- The embedder and store are **injected** (`embedder_factory` / `store_factory` on
  `RegulatoryDocsHandler`), so the agent is fully testable offline.

## Demo app

```bash
uv sync --extra regulatory_docs        # or: cd agents/regulatory_docs && uv sync
uv run uvicorn agents.regulatory_docs.demo.main:app --port 8110   # open http://localhost:8110
```

Paste or upload a document (Markdown / txt / PDF), search and select one, choose a vectorization
profile, and push it to Qdrant.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/upload` | Upload pasted text. |
| `POST /api/upload_file` | Upload a file (multipart). |
| `GET /api/documents?query=` | List or search the library. |
| `GET /api/profiles` | Vectorization profiles. |
| `POST /api/vectorize` | Vectorize `{doc_id, profile}`. |
| `GET /api/health` | Health check. |

The `default` and `openai-large` profiles need `OPENAI_API_KEY` and
`QDRANT_URL` / `QDRANT_API_KEY`; the `offline` profile needs nothing.

## Configuration

| Var | Default | Purpose |
| --- | --- | --- |
| `REGDOCS_DIR` | `agents/regulatory_docs/storage/documents` | Document library location. |
| `VECTORIZATION_CONFIG` | `config/vectorization.json` | Vectorization profiles. |
| `OPENAI_API_KEY` | — | OpenAI embeddings. |
| `QDRANT_URL` / `QDRANT_API_KEY` | — | Cloud `main` cluster. |
| `QDRANT_URL_KG` / `QDRANT_API_KEY_KG` | — | Optional cloud `kg` cluster. |

## Tests

`tests/regulatory_docs/` (32 tests) use the `hash` embedder and an in-memory vector store.
`test_cloud_qdrant.py` also writes to a throwaway collection on the **live cloud Qdrant** (and
deletes it) when `QDRANT_URL` / `QDRANT_API_KEY` are set; otherwise it skips.

## Next steps

- A `search` intent to query a collection (feeding the semantic cache and verification agents).
- Coordinator intent recognition for `upload_document` / `find_documents` / `vectorize_document`.
- End-to-end checks of large-document batching with OpenAI embeddings.
