# Coordinator Agent — Refactor Plan (for review)

**Status:** proposal only. **No source code is changed by this document.** It describes the
target architecture, the new module layout, and a phased migration. Review and adjust before we
touch code.

Related: [`todo/IMPLEMENTATION_PLAN.md`](./IMPLEMENTATION_PLAN.md) already anticipates an
"orchestrator agent that delegates to specialized sub-agents". This plan makes that concrete and
adds two new responsibilities the current design does not yet cover: **user-project management**
and a **semantic cache** for finding existing/similar projects.

---

## 1. Goals

1. Introduce a **coordinator agent** that is the single entry point for the user, recognizes
   intent from natural language, and delegates to specialized **sub-agents**.
2. Let the coordinator **create, save, load, list and manage user projects** (persisted across
   sessions, keyed to the user).
3. Let the coordinator decide, per request, whether to **start a new project**, **continue an
   existing one**, or **surface similar existing projects** via a **semantic cache**
   (interface + wiring now; embedding/search implementation **deferred** — provisioned but not
   built).
4. Turn the **current pipeline** (ingest → evidence → IR → terminology → BPMN/DMN) into **one
   sub-agent**: the **text→BPMN agent**. Keep its internals (`src/…` pipeline) **unchanged**;
   only add a thin service/adapter wrapper around it.
5. Restructure so that **any sub-agent can be either in-process or external** (its own HTTP
   service / API), behind one agent contract. Follow standard service-boundary practices so an
   agent can be moved out-of-process without changing the coordinator.

### Non-goals (this refactor)
- No change to BPMN/DMN generation behavior or the pipeline stages.
- No implementation of the semantic embedding/search itself (only the interface + a no-op/stub).
- No new front-end features beyond pointing the existing UI at the coordinator.

---

## 2. Current state (baseline)

- `src/` is a clean, staged, provider-agnostic pipeline (`src/pipeline/runner.py` orchestrates
  ingest → evidence → interpret → terminology → BPMN/DMN). Well-tested (46 tests, offline).
- `app/main.py` is a single FastAPI app with `POST /api/generate` that runs the whole pipeline
  on submitted text and returns BPMN/DMN XML + extracted resources. Static UI at `/`.
- `src/pipeline/artifacts.py` already persists **per-run** versioned JSON artifacts under
  `runs/<run_id>/`. There is **no** concept of a *user project* spanning runs.
- `src/retrieval/` already defines an `EvidenceIndex` interface with an in-memory impl and a lazy
  `QdrantEvidenceIndex`. `api/qdrant_remote_client.py` has working Qdrant connection helpers and
  `.env` already carries `QDRANT_URL` / `QDRANT_API_KEY`. This is the natural backing for the
  semantic cache later.
- `src/llm/` provides a swappable `LLMProvider` with structured-JSON output — reuse it for intent
  classification (structured, not free prose).

**Implication:** the current app *is* the text→BPMN agent. We wrap it, we do not rewrite it.

---

## 3. Target architecture

```
                        ┌──────────────────────────────────────────────┐
        user  ───────▶  │            COORDINATOR AGENT                  │
   (NL request)         │  (FastAPI app, own port, e.g. :8000)         │
                        │                                              │
                        │  1. intent recognition (LLM structured)      │
                        │  2. project resolution:                      │
                        │       new  |  continue(existing)  |  similar │
                        │       └─ SemanticProjectCache (stub for now) │
                        │  3. route to sub-agent via AgentRegistry     │
                        │  4. persist result into the Project          │
                        └───────┬───────────────────────┬──────────────┘
                                │                        │
                 in-process     │                        │  HTTP / API (best practice)
                 adapter        │                        │
                                ▼                        ▼
                   ┌───────────────────────┐   ┌───────────────────────────┐
                   │  TEXT→BPMN AGENT       │   │  (future external agents)  │
                   │  wraps existing src/   │   │  e.g. simulation, critic,  │
                   │  pipeline UNCHANGED    │   │  KG, other teams' services │
                   │  own service :8100     │   │  own ports / remote URLs   │
                   └───────────────────────┘   └───────────────────────────┘

     Persistence:   ProjectStore (JSON on disk now; pluggable)  +  runs/ artifacts (unchanged)
     Provisioned:   SemanticProjectCache (Qdrant-backed later)  ← interface only for now
```

Key principles (consistent with the repo's existing philosophy):
- **Deterministic, code-orchestrated delegation.** The coordinator uses the LLM only to *classify
  intent / extract parameters* (structured output), never to decide control flow by free prose.
- **One agent contract, two transports.** Every sub-agent — local or remote — implements the same
  request/response contract. The coordinator talks to an `Agent` handle and does not care whether
  it is in-process or an HTTP service.
- **Agents are independently runnable.** The text→BPMN agent can run embedded *or* as its own
  service on its own port; moving it out is a config change, not a code change.

---

## 4. The agent contract (local + external)

A single versioned contract, defined once as Pydantic models, shared by every agent regardless of
transport.

**Discovery / capabilities** — every agent advertises what intents it serves:
```
GET  /agent/manifest   ->  { name, version, capabilities: [ {intent, input_schema, output_schema} ], async: bool }
GET  /health           ->  { status: "ok" }
```

**Invocation** — because the pipeline is long-running (multiple LLM calls), support an async
job pattern (best practice for remote agents) with a sync convenience path:
```
POST /agent/invoke        ->  { job_id }                # accepts {intent, project_id, payload}
GET  /agent/jobs/{id}     ->  { status, result?, error? }  # queued|running|succeeded|failed
POST /agent/invoke_sync   ->  { result }                # convenience for fast/local calls
```

Shared schemas (new module, see §6):
- `AgentRequest { intent, project_id, correlation_id, payload }`
- `AgentResult  { agent, intent, status, output, artifacts_ref, error }`

Transport adapters (coordinator side), both implementing one `Agent` protocol
(`manifest()`, `invoke()`, `poll()`):
- `InProcessAgent` — calls the wrapped Python object directly (used for text→BPMN today; zero
  network overhead, easiest to test).
- `HttpAgent` — calls a remote agent over HTTP using the contract above (timeouts, retries with
  backoff, correlation-id propagation, error normalization).

**Registry.** An `AgentRegistry` loads an `agents.yaml`/`config/agents.json` manifest:
```
agents:
  - name: text_to_bpmn
    transport: in_process           # or: http
    intents: [engineer_process]
    # when transport == http:
    # base_url: http://localhost:8100
    # timeout_s: 300
```
Switching the text→BPMN agent from embedded to remote = flip `transport` + set `base_url`. No
coordinator code change.

> **Standards note (for the reviewer):** the contract above is deliberately a small, explicit
> REST+JSON shape. If you prefer to align with an emerging standard we can instead adopt
> **MCP** (tools/resources) or the **A2A** agent-card model for `/manifest`. Recommendation:
> start with the minimal REST contract (least ceremony, fully under our control) and keep the
> manifest shape close to A2A's "agent card" so migration stays cheap. Flag your preference.

---

## 5. Proposed directory layout

Keep the existing `src/` pipeline package **exactly where it is**. Add new top-level packages.
`pyproject.toml` already sets `pythonpath = ["."]`, so sibling top-level packages import cleanly.

```
agents/                         # NEW — everything agent/coordination related
  contract/                     # the shared agent contract (§4)
    __init__.py
    models.py                   # AgentRequest / AgentResult / Manifest / Capability
    base.py                     # Agent protocol; InProcessAgent + HttpAgent adapters
    registry.py                 # AgentRegistry (loads config/agents.json)

  coordinator/                  # NEW — the coordinator agent
    __init__.py
    intent.py                   # LLM structured intent classifier (reuses src/llm)
    coordinator.py              # core: intent -> project resolution -> route -> persist
    projects/                   # user-project management
      models.py                 # Project, ProjectRef, ProjectStatus
      store.py                  # ProjectStore protocol
      json_store.py             # filesystem/JSON impl (mirrors ArtifactStore style)
    cache/                      # SEMANTIC CACHE — provisioned, not implemented
      base.py                   # SemanticProjectCache protocol (+ SimilarProject result)
      noop.py                   # NoOpSemanticCache (default; always "no match")
      qdrant.py                 # QdrantSemanticCache stub -> raises NotImplementedError
    service.py                  # coordinator FastAPI app (own port, e.g. :8000)

  text_to_bpmn/                 # NEW — thin wrapper making current pipeline an agent
    __init__.py
    agent.py                    # adapter: AgentRequest -> src.pipeline.Pipeline -> AgentResult
    service.py                  # standalone FastAPI service exposing the agent contract (:8100)

config/
  agents.json                   # NEW — agent registry manifest (which agents, transport, urls)
  terminology.json              # (existing, unchanged)

app/                            # existing UI + current API
  main.py                       # (existing generate endpoint — see migration §11 for its fate)
  static/                       # UI; point it at the coordinator endpoint

src/                            # UNCHANGED pipeline package (ingest…bpmn/dmn)
runs/                           # UNCHANGED per-run artifacts
projects/                       # NEW — persisted user projects (JSON), gitignored like runs/
```

Rationale for a top-level `agents/` (vs `src/agents/`): it signals a **service boundary** — these
are deployable units, some of which will live in other processes/repos — while `src/` stays the
in-process library the text→BPMN agent is built from. If you'd rather keep everything under `src/`
for packaging simplicity, that's a one-line change to this layout — flag it.

---

## 6. Coordinator design

### 6.1 Intent recognition (`coordinator/intent.py`)
- Use `LLMProvider.generate_json` (already structured) with a small JSON schema to classify the
  user message into an intent + extracted slots. Initial intent set:
  - `engineer_process` — user wants a BPMN from a description/document → text→BPMN agent.
  - `new_project` — start fresh.
  - `open_project` — continue a named/identified project.
  - `find_project` — search existing/similar projects (drives the semantic cache).
  - `list_projects` / `project_status` — management queries.
  - `unknown` — fall back to a clarifying question (never guess-and-run).
- Deterministic guards around the classifier (e.g. explicit "new project" phrasing, or a
  `project_id` already in session, short-circuit the LLM). LLM decides *classification*, code
  decides *control flow*.

### 6.2 Request lifecycle
```
message ─▶ classify intent + slots
        ─▶ resolve project:
             if new_project            -> create Project (after cache "similar?" check)
             elif project_id in slots  -> load Project
             elif find_project         -> SemanticProjectCache.find_similar(text) [stub -> []]
             else                      -> ask clarifying question
        ─▶ registry.get(intent).invoke(AgentRequest{intent, project_id, payload})
        ─▶ on success: attach run/artifacts to Project; ProjectStore.save(project)
        ─▶ return AgentResult + project summary to caller
```

### 6.3 Coordinator service (`coordinator/service.py`)
FastAPI app on its own port exposing:
- `POST /coordinator/message` — main NL entry point `{ user_id, project_id?, text }`.
- `GET  /coordinator/projects` — list user's projects.
- `GET  /coordinator/projects/{id}` — project detail (runs, artifacts, history).
- `GET  /health`.
The existing UI calls this instead of calling `/api/generate` directly.

---

## 7. User-project management

### 7.1 Model (`coordinator/projects/models.py`)
```
Project {
  id: str                         # stable slug/uuid
  user_id: str                    # e.g. the authenticated user's email
  name: str
  description: str                # used later as the semantic-cache embedding source
  status: draft | in_progress | complete | archived
  created_at / updated_at: datetime
  runs: [str]                     # run_ids produced by sub-agents (link to runs/ artifacts)
  history: [ {ts, intent, agent, summary} ]   # audit trail of coordinator actions
  tags: [str]
}
ProjectRef { id, name, status, updated_at }   # lightweight list item
```

### 7.2 Store (`coordinator/projects/store.py` + `json_store.py`)
- `ProjectStore` protocol: `create`, `get`, `list(user_id)`, `save`, `archive`, `search(text)`
  (text search delegated to the semantic cache; falls back to naive substring for now).
- `JsonProjectStore` — one file per project under `projects/<user_id>/<project_id>.json`, written
  atomically; mirrors the immutable/append style of `ArtifactStore`. Pluggable so a DB
  (Postgres/Neo4j — creds already present) can replace it later without coordinator changes.
- Projects **reference** runs; artifacts stay in `runs/` (no data duplication).

---

## 8. Semantic cache (provisioned, NOT implemented)

Purpose: when the user starts something new, detect whether an existing/similar project already
covers it (dedupe, resume, reuse), and power `find_project`.

Provide the seam now; leave the embedding/search for later:
- `cache/base.py` — `SemanticProjectCache` protocol:
  ```
  index_project(project) -> None            # add/update project embedding
  find_similar(query: str, k=5, min_score=…) -> list[SimilarProject]   # SimilarProject{ref, score}
  ```
- `cache/noop.py` — `NoOpSemanticCache`: `index_project` = no-op, `find_similar` = `[]`.
  **This is the wired-in default**, so the whole flow runs today with "no similar projects".
- `cache/qdrant.py` — `QdrantSemanticCache` **skeleton**: constructor accepts an embedder +
  Qdrant client (reuse `api/qdrant_remote_client.py` / `src/retrieval/qdrant_index.py` patterns),
  methods raise `NotImplementedError("semantic cache deferred — see COORDINATOR_PLAN §8")`.
- Coordinator calls `find_similar` at `new_project`/`find_project` decision points and simply gets
  `[]` today; the branch that would present "did you mean these existing projects?" is written but
  currently a no-op path. When implemented later, only `qdrant.py` + a config flag change.

Config flag (in `config/agents.json` or settings): `semantic_cache: "noop" | "qdrant"`.

---

## 9. Text→BPMN agent (wrap, don't touch)

- `text_to_bpmn/agent.py` — an `InProcessAgent`-compatible adapter whose `invoke()`:
  1. builds `Settings` + selects provider (lift the `_select_provider` logic currently in
     `app/main.py` — move, don't modify),
  2. runs the existing `Pipeline` exactly as `app/main.py` does today (`ingest_text` → … →
     `compile_bpmn`), and the existing `_resources(...)` shaping,
  3. returns an `AgentResult` carrying `bpmn_xml`, `dmn_xml`, `resources`, `validation`, `run_id`.
  **The `src/` pipeline code is not edited.** This adapter is the only new code, and it is a
  translation layer between the contract and the current function calls.
- `text_to_bpmn/service.py` — a standalone FastAPI app exposing `/agent/manifest`,
  `/agent/invoke`, `/agent/jobs/{id}`, `/health` on its own port (e.g. `:8100`), so the agent can
  run as an external service. Long runs use a background task + the `runs/` store as the job store.
- Because the adapter satisfies the same `Agent` protocol as `HttpAgent`, the coordinator can call
  the text→BPMN agent **in-process** (default, fast, current behavior) or **over HTTP** (when
  `config/agents.json` sets `transport: http`) with no coordinator code change.

---

## 10. Configuration & deployment

- New env / settings (extend `src/config.py` `Settings` — additive, no breaking change):
  - `COORDINATOR_PORT` (default 8000), `TEXT_TO_BPMN_PORT` (default 8100),
  - `PROJECTS_DIR` (default `<repo>/projects`),
  - `SEMANTIC_CACHE` (`noop` default),
  - `AGENTS_CONFIG` (default `config/agents.json`).
- Run modes:
  - **All-in-one (dev, default):** coordinator imports the text→BPMN agent in-process. One
    process, current behavior preserved.
  - **Distributed:** run `text_to_bpmn/service.py` on `:8100`, set its registry entry to
    `transport: http`, run `coordinator/service.py` on `:8000`. Demonstrates the external-agent
    path.
- Add `projects/` to `.gitignore` (like `runs/`).
- `pyproject.toml`: no new hard deps for the default path (`httpx` already present in `dev` for the
  HTTP adapter/tests; promote to a runtime dep only when the HTTP transport is enabled). Qdrant
  stays optional (`.[retrieval]`) since the cache is deferred.

---

## 11. Migration plan (phased, behavior-preserving)

Each phase is independently reviewable; the BPMN output is byte-for-byte unchanged throughout.

- **Phase A — Contracts & registry (no behavior change).** Add `agents/contract/` (models, base,
  registry) + `config/agents.json` with a single in-process `text_to_bpmn` entry. Unit tests only.
- **Phase B — Wrap the pipeline.** Add `text_to_bpmn/agent.py` (adapter) reusing the exact logic
  from `app/main.py`. Add a test asserting the adapter's output matches the current
  `/api/generate` output for the fixtures.
- **Phase C — Project store.** Add `coordinator/projects/` (+ `JsonProjectStore`) with tests.
- **Phase D — Semantic cache seam.** Add `coordinator/cache/` with `NoOpSemanticCache` wired as
  default; `QdrantSemanticCache` stub. Tests assert `find_similar -> []`.
- **Phase E — Coordinator core + service.** Add `coordinator/intent.py`, `coordinator.py`,
  `service.py`. Wire intent → project resolution → registry → persist. Tests with a scripted mock
  LLM for intent classification.
- **Phase F — Externalize the BPMN agent.** Add `text_to_bpmn/service.py`; add an integration test
  that flips the registry to `transport: http` and exercises `HttpAgent` end-to-end.
- **Phase G — UI cutover.** Point `app/static` at `/coordinator/message`. Decide the fate of
  `app/main.py` `/api/generate`: **keep** as a thin proxy/back-compat shim, or **retire** in favor
  of the text→BPMN service. (Recommend: keep as a deprecated shim for one release.)

---

## 12. Testing strategy
- Contract adapters: `InProcessAgent`/`HttpAgent` conform to one protocol (shared test suite;
  HTTP tested against the text→BPMN service via `httpx` `TestClient`).
- Golden equivalence: text→BPMN adapter output == current `/api/generate` output on the 6 fixtures.
- Coordinator: intent classification with a scripted mock provider; project lifecycle (create →
  run → persist → reload); cache branch returns `[]` today.
- No network / no API keys required for the default (in-process, noop-cache) test path — matches
  the repo's current offline test discipline.

---

## 13. Open questions for the reviewer

1. **Package root:** top-level `agents/` (signals service boundaries, my recommendation) vs
   everything under `src/` (simpler packaging). Which do you prefer?
2. **Agent protocol:** minimal REST+JSON (recommended) vs align to **MCP** or **A2A** agent-card.
3. **User identity:** is `user_id` = the authenticated email (`natallia.kokash@gmail.com` seen in
   session) enough, or do we need real auth before persisting projects?
4. **Project store backend:** JSON-on-disk first (recommended) — or go straight to Postgres/Neo4j
   (creds already in `.env`)?
5. **`/api/generate` fate** at cutover: deprecated shim vs immediate retire.
6. **Semantic cache embedder** (deferred): reuse the same embedding model as evidence retrieval,
   or a dedicated project-description embedding? (Decide when we implement §8.)

---

*Awaiting review. On approval I'll implement Phase A first and stop for check-in.*
