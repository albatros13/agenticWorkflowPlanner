# Shared storage (cross-agent exchange)

Top-level storage for data **shared between agents** — the exchange area for artifacts one agent
produces and another consumes (e.g. a future semantic project cache, shared embeddings, or
hand-off payloads).

Location and backend are declared in [`config/storage.json`](../config/storage.json) under
`shared`. The default backend is `local` (this folder); swap it for a cloud store (object store,
Postgres, Qdrant, …) by changing `kind` + params there — no agent code changes.

Contents are runtime data and are **gitignored**; only this README is tracked.

Private per-agent storage lives under each agent instead: `agents/<agent>/storage/`.
