# regulatory_docs — private storage

Catalogue of uploaded regulatory documents, one directory per document (keyed by a content
hash), each holding:

- `documents/<doc_id>/document.json` — the parsed `Document` (structure + source locations)
- `documents/<doc_id>/meta.json` — the `DocumentRecord` the UI lists/selects, including which
  vectorizations have been run

Vectors themselves are **not** stored here — they live in the Qdrant cluster named by the chosen
profile in `config/vectorization.json`.

The location is configured in `config/storage.json` (`agents.regulatory_docs`) and can be
overridden for tests/ops with the `REGDOCS_DIR` env var.
