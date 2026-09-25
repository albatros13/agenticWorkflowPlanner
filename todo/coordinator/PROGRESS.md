# Coordinator Agent — PROGRESS

Refactor implementing [`todo/COORDINATOR_PLAN.md`](../../todo/COORDINATOR_PLAN.md) and the project
lifecycle from [`todo/PROJECT_STATE_MANAGEMENT.md`](../../todo/PROJECT_STATE_MANAGEMENT.md).

**Status: Phases A–F complete.** `pytest -q` → **81 passed** (was 46), fully offline (no API keys).
The BPMN pipeline is **unchanged** (only relocated, see below); the demo `/api/generate` output is
byte-identical.

> **Layout update:** each agent now owns its full source under its own folder. The pipeline moved
> from the top-level `src/` into `agents/text_to_bpmn/pipeline/` (imports updated; `REPO_ROOT`
> re-anchored; `agents/paths.py` no longer depends on any agent). The shared contract stays in
> `agents/contract/`.

---

## What was built

### 1. Agent contract + registry (`agents/contract/`)
- `models.py` — shared, versioned request/response schemas: `AgentRequest`, `AgentResult`,
  `AgentManifest`/`Capability` (discovery), `Job`/`JobStatus` (async).
- `base.py` — one `Agent` surface, two transports:
  - `InProcessAgent` wraps an `AgentHandler` (in-process business logic);
  - `HttpAgent` speaks the REST contract to a remote agent (lazy `httpx`, normalized errors).
- `registry.py` — `AgentRegistry.from_config()` loads `config/agents.json`, builds in-process or
  http agents, and maps **intents → agents**. Flipping an agent from embedded to remote is a
  config change (`transport: http` + `base_url`), no coordinator code change.

### 2. text→BPMN agent (`agents/text_to_bpmn/`) — the current code, wrapped not rewritten
- `shaping.py` — provider selection + resource shaping **lifted verbatim** from `app/main.py`
  (single source of truth).
- `agent.py` — `TextToBpmnHandler` runs the existing `Pipeline` exactly as before and returns an
  `AgentResult`. Provider is injectable (tests / coordinator) or env-selected.
- `service.py` — standalone FastAPI service (`/agent/manifest`, `/agent/invoke_sync`,
  `/agent/invoke` + `/agent/jobs/{id}`, `/health`) so the agent can run **externally** on its own
  port (`:8100`).
- `app/main.py` refactored into a thin back-compat shim that delegates to `TextToBpmnHandler`
  (existing `test_api.py` still passes unchanged).

### 3. Project management (`agents/coordinator/projects/`)
- `models.py` — `Project` (id, user_id, name, description, **state**, runs[], history[], tags) +
  `ProjectRef`, `HistoryEntry` (audit trail). Projects *reference* `runs/` artifacts (no dup).
- `store.py` / `json_store.py` — `ProjectStore` protocol + atomic per-user JSON store under
  `projects/<user_id>/<id>.json`. Pluggable for a DB later.

### 4. Project lifecycle state machine (`agents/coordinator/state/`)
- `machine.py` — data-driven FSM loading **`config/project-states.json`** (copied from the
  `todo/` design doc as the canonical runtime location). Answers: allowed actions per state,
  next state per action, and the two global actions:
  - `deleteProject` → terminal from any state;
  - `reviseDescription` → stays put if semantics unchanged, else jumps to `MODEL_IN_PROGRESS` and
    flags `invalidated` (prior verification/approvals must be redone).
- This is what **constrains the coordinator's response**: a request whose action the current state
  forbids is *refused*, not executed.

### 5. Semantic cache seam (`agents/coordinator/cache/`) — provisioned, not implemented
- `base.py` — `SemanticProjectCache` protocol (`index_project`, `find_similar`) + `SimilarProject`.
- `noop.py` — `NoOpSemanticCache`, the **wired-in default** (`find_similar → []`), so the whole
  flow runs today.
- `qdrant.py` — `QdrantSemanticCache` **skeleton** (raises `NotImplementedError`); reuses the
  existing Qdrant creds/pattern. Selected via `SEMANTIC_CACHE=noop|qdrant`.

### 6. Coordinator core + service (`agents/coordinator/`)
- `intent.py` — `IntentClassifier` with a `HeuristicIntentClassifier` (offline default) and an
  `LLMIntentClassifier` (structured JSON via the existing `LLMProvider`).
- `coordinator.py` — the orchestration loop: **classify intent → resolve project (new / open /
  find-similar) → map intent to a lifecycle action → gate it through the state machine → route to
  the sub-agent via the registry → apply transition, record history, persist.** Returns a
  `CoordinatorResponse` that always includes the current state + `allowed_actions`.
- `factory.py` — `build_coordinator()` assembles registry + store + machine + cache + classifier
  from config/env.
- `service.py` — coordinator FastAPI app (`:8000`): `POST /coordinator/message`,
  `GET /coordinator/projects`, `GET /coordinator/projects/{id}`, `DELETE /coordinator/projects/{id}`,
  `/health`, and the chat UI at `/`.

### 7. Chat UI (`agents/coordinator/static/index.html`)
Single-page front end served at `/`:
- **Chat window** to submit intent in natural language (Enter to send); coordinator replies show the
  recognized intent, current state, and clickable `allowed_actions` chips.
- **Project sidebar** listing the user's projects with lifecycle-state badges; **select** (click),
  **delete** (✕, confirms), and **+ New** to start a fresh project.
- **Inline BPMN rendering** (bpmn-js) with validation badges + `.bpmn` download whenever the
  text→BPMN agent returns a model; **similar-project** suggestions render as clickable chips.
- Editable `user_id` field (single-user default until auth); quick-suggestion chips incl. a sample.

### Config
- `config/agents.json` — registry manifest (ships `text_to_bpmn` as `in_process`).
- `config/project-states.json` — canonical lifecycle spec.
- `agents/paths.py` — env-overridable locations (`AGENTS_CONFIG`, `PROJECT_STATES_CONFIG`,
  `PROJECTS_DIR`). `.gitignore` now excludes `projects/`.

---

## Tests added (35 new, all offline)
`test_agent_contract.py`, `test_state_machine.py`, `test_project_store.py`,
`test_text_to_bpmn_agent.py`, `test_text_to_bpmn_service.py` (incl. `HttpAgent` via ASGI),
`test_coordinator.py`, `test_coordinator_service.py`, plus shared `_bpmn_fixtures.py`.

---

## How to run

```bash
# All-in-one (default): coordinator imports the text→BPMN agent in-process
uvicorn agents.coordinator.service:app --port 8000

# Distributed: run the agent as an external HTTP service, then set config/agents.json
#   text_to_bpmn -> { "transport": "http", "base_url": "http://localhost:8100" }
uvicorn agents.text_to_bpmn.service:app --port 8100
uvicorn agents.coordinator.service:app  --port 8000
```

Design decisions taken (COORDINATOR_PLAN §13): top-level `agents/` package; minimal REST+JSON
contract; `user_id` = email (single default user until auth); JSON-on-disk store; `/api/generate`
kept as a deprecated shim; semantic cache = `noop` default.

---

## Deferred / next
- Implement `QdrantSemanticCache` (embedding + upsert + search) — §8 of the plan.
- Add real sub-agents behind the same contract (verification/critic, simulation) — these are the
  intended **external** agents; `verify_model` is recognized + state-gated but returns
  "agent not available" until one is wired.
- Real auth for `user_id`; optional DB-backed `ProjectStore`.
- UI cutover: point `app/static` at `/coordinator/message`.
