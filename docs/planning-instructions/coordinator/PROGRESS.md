# Coordinator Agent — PROGRESS

Refactor implementing [`todo/COORDINATOR_PLAN.md`](../COORDINATOR_PLAN.md) and the project
lifecycle from [`todo/PROJECT_STATE_MANAGEMENT.md`](../PROJECT_STATE_MANAGEMENT.md).

**Status: Phases A–F complete.** `pytest -q` → **81 passed** (was 46), fully offline (no API keys).
The BPMN pipeline is **unchanged** (only relocated, see below); the demo `/api/generate` output is
byte-identical.

> **Layout update:** each agent now owns its full source under its own folder. The pipeline moved
> from the top-level `src/` into `agents/text_to_bpmn/pipeline/` (imports updated; `REPO_ROOT`
> re-anchored; `agents/paths.py` no longer depends on any agent). The shared contract stays in
> `agents/contract/`.

> **Update 2026-11-06:** the coordinator now routes every registered agent from chat. See
> [Agent capability recognition](#update-2026-11-06-agent-capability-recognition) at the end. The
> sections before it are the original Phase A–F record, so their test counts and "Deferred / next"
> list are from that time.

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
  port (`:8120`).
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
#   text_to_bpmn -> { "transport": "http", "base_url": "http://localhost:8120" }
uvicorn agents.text_to_bpmn.service:app --port 8120
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

---

## Update 2026-11-06: agent capability recognition

**Goal:** the coordinator should recognise the capabilities of registered agents and call them when
a user request calls for it.

**Starting point:** `verify_eflint` was already routed to `eflint_verifier`. The gap was elsewhere:

- The `regulatory_docs` and `text_to_eflint` intents were registered in `config/agents.json`, but the
  classifier never emitted them.
- `regulatory_docs` had no handler in `factory.py`, so the registry skipped it.
- The classifier's intent list and prompt were hard-coded, so each new agent needed coordinator
  changes.

**Status:** implemented, not committed. The offline suite has 368 passed and 1 failed; the failure
is an existing bug unrelated to this work ([Test results](#test-results)).

### What changed

**Capability discovery**

- `AgentRegistry.capabilities()` lists each routable intent with its agent and description. The
  description comes from the agent's manifest, or from `config/agents.json` if a remote agent is down.
- `LLMIntentClassifier` builds its allowed intents and prompt from those capabilities, so an agent
  that isn't available can't be chosen.
- `HeuristicIntentClassifier` recognises a new intent by its name at the start of a message:
  `summarize_document` matches "summarize document ...".
- An intent with no dedicated handler is forwarded by `Coordinator._route_registered` with
  `{text, ...slots}`, plus `attachments` and `bpmn_xml` if present.
- "What can you do?" lists the capabilities, and they are also part of the reply when a message isn't
  understood.

To add an agent, register it in `config/agents.json` and add its handler to `factory.py`. A remote
`http` agent needs only the config entry. See "Adding an agent" in
[`agents/coordinator/README.md`](../../../agents/coordinator/README.md#adding-an-agent).

**New routes**

| Intent | Agent | Behaviour |
| --- | --- | --- |
| `upload_document` | `regulatory_docs` | Adds a pasted document to the library. |
| `find_documents` | `regulatory_docs` | Lists the library, or looks documents up by title, id or preview. |
| `vectorize_document` | `regulatory_docs` | Embeds a document named by id or title, with an optional profile. Asks if the name is ambiguous. |
| `list_vectorized` | `regulatory_docs` | Lists the documents in the Qdrant collections. Reports an unreachable collection without failing the rest. |
| `search_vectors` | `regulatory_docs` | Semantic search over document content, with snippets. |
| `generate_eflint` | `text_to_eflint` | Regulation to eFLINT. Source order: pasted document, text after a colon, named library document, the message itself if long. |
| `validate_eflint` | `text_to_eflint` | Checks a pasted spec or the project's spec. Stores nothing. |
| `list_capabilities` | coordinator | Lists the registered agents and what they do. |

**Design decisions**

- **A generated spec feeds verification.** A spec that passes its checks is saved as the project's
  `spec.eflint`, which `verify_eflint` picks up. A spec that needs review is returned but never replaces
  the stored one. Qualification rules still have to be pasted.
- **No file paths in chat uploads.** A chat message must not be able to make the server read its own
  files, so the coordinator never passes the agent's `path` input. Files go through the
  `regulatory_docs` demo app.
- **Commands must start the message.** Commands are matched at the start of a message, in the part
  before any colon, up to 20 words. A process description that mentions documents or searching is
  still modelled as a process.
- **Pasted documents stay out of the classifier.** A ```` ```regulation ````, ```` ```document ```` or
  ```` ```markdown ```` block is split off in code. Text after a colon does reach the classifier, so
  long documents should be pasted as a block.
- **The lifecycle doesn't change.** Document and eFLINT authoring operations only record history on an
  open project, and only `upload`, `vectorize` and `generate` write to it. The document library is
  shared across projects.

**Files changed**

| File | Change |
| --- | --- |
| `agents/contract/registry.py`, `__init__.py` | `AgentCapability`, `AgentRegistry.capabilities()`, config descriptions. |
| `agents/contract/base.py` | Manifest fetch limited to 10 s, so a hung remote agent can't stall start-up. |
| `agents/coordinator/intent.py` | New intents, command recognition, capability-driven prompt and schema, `document` attachments. |
| `agents/coordinator/coordinator.py` | Handlers for the new routes, document name resolution, generic forwarding. |
| `agents/coordinator/factory.py` | Wires `RegulatoryDocsHandler`; passes the registry to the classifier. |
| `agents/coordinator/static/index.html` | eFLINT badges and download button; updated input hint. |
| `tests/test_coordinator_capabilities.py` | 56 new tests. |
| `README.md`, READMEs of `coordinator`, `regulatory_docs`, `text_to_eflint`, `eflint_verifier` | Updated the statements that are no longer true. |

### Example output

All output below is verbatim from runs on 2026-11-06, with no API keys and no network calls.

#### Through the real factory and HTTP service

Run with `build_coordinator()`, `POST /coordinator/message` and the heuristic classifier, using a
temporary `hash` + `memory` vectorization profile. All five agents registered:

```text
registered: ['eflint_verifier', 'petri_net_generator', 'regulatory_docs', 'text_to_bpmn', 'text_to_eflint']
```

The `memory` cluster opens a new client per call, so vectors don't persist between messages. That is
why the list and search replies below are empty straight after a successful vectorization. The next
scenario uses a shared store and does return hits.

````text
>>> 'what can you do?'
[200] intent=list_capabilities needs_input=False
I can manage your projects (new, open, list, find, status, revise, delete) and call these agents:
- verify eflint (eflint_verifier): Petri net + eFLINT + qualification rules (+ analysis) -> verdict with witnesses.
- generate petri net (petri_net_generator): BPMN XML / IR / text -> workflow Petri net (PNML).
- find documents (regulatory_docs): List or search uploaded documents; returns vectorization options.
- list vectorized (regulatory_docs): List documents stored in Qdrant, per profile collection.
- search vectors (regulatory_docs): Semantic search over a profile's Qdrant collection.
- upload document (regulatory_docs): Register a regulatory document from raw text or a file path.
- vectorize document (regulatory_docs): Vectorize a selected document into Qdrant using a config profile.
- engineer process (text_to_bpmn): Compile a process description into BPMN/DMN with provenance.
- generate eflint (text_to_eflint): Regulatory text -> validated eFLINT (+ scenarios).
- validate eflint (text_to_eflint): Check an eFLINT spec and run its scenarios (no LLM).

>>> 'Upload this regulation:\n```regulation\n# Retention '
[200] intent=upload_document needs_input=False
Added 'Retention Rules' to the document library (id c4c6befa0358, 3 blocks). Say 'vectorize document c4c6befa0358' to make it searchable.

>>> 'list the regulatory documents'
[200] intent=find_documents needs_input=False
1 document(s):
- Retention Rules (id c4c6befa0358)
Vectorization profiles: offline (default).

>>> 'vectorize the retention rules document'
[200] intent=vectorize_document needs_input=False
Vectorized 'Retention Rules': 2 chunks in collection 'regs' (cluster memory, model local-hash). You can now search it.

>>> 'which documents are vectorized?'
[200] intent=list_vectorized needs_input=False
regs (cluster memory): no documents

>>> 'search the regulations for personal data'
[200] intent=search_vectors needs_input=False
No passages found for 'personal data' in collection 'regs'. Has a document been vectorized with this profile?

>>> 'verify the process against the eFLINT norms'
[200] intent=verify_eflint needs_input=True
To verify the process against an eFLINT specification I still need a process model (PNML / .pn Petri net, or a BPMN model), an eFLINT specification, qualification rules. Paste each as a fenced code block in your message — ```eflint, ```qualification (rules mapping process steps to eFLINT acts/events, ending in '.'), optionally ```analysis (properties; otherwise I check that no violation occurs), and ```pnml or ```pn for the Petri net. They are kept with the project for later runs. I can also generate the eFLINT specification from a regulation: ask me to 'generate eFLINT from <document>'.

>>> 'generate eflint'
[200] intent=generate_eflint needs_input=True
To generate eFLINT I need the regulation: paste it in a ```regulation block, write it after a colon ('Generate eFLINT: ...'), or name a document from the library ('generate eFLINT from <title or id>').

>>> 'hello there'
[200] intent=unknown needs_input=True
I couldn't tell what you'd like to do. I can manage your projects (new, open, list, find, status, revise, delete) and call these agents:
[same capability list as the first reply]
````

The last reply is shortened where marked; everything else is complete.

#### Full flow on an open project

Run with an in-memory vector store shared between messages, hash embeddings and a scripted LLM. This
shows the coordinator's routing and replies, not the quality of real results:

- The scripted LLM returns the same canned GDPR consent spec for every request. The 4 warnings on
  'Retention Rules' come from that spec not matching the document.
- Hash embedding scores carry no meaning, so the ranking and scores below aren't evidence of search
  quality.

```text
>>> 'Upload this regulation titled "Retention Rules"\n```regulation\n# Retent'
intent=upload_document needs_input=False state=INTENT_CAPTURED
Added 'Retention Rules' to the document library (id c4c6befa0358, 3 blocks). Say 'vectorize document c4c6befa0358' to make it searchable.

>>> 'Vectorize the Retention Rules document'
intent=vectorize_document needs_input=False state=INTENT_CAPTURED
Vectorized 'Retention Rules': 2 chunks in collection 'regs' (cluster memory, model local-hash). You can now search it.

>>> 'Which documents are vectorized?'
intent=list_vectorized needs_input=False state=INTENT_CAPTURED
regs (cluster memory): Retention Rules [2 chunks]

>>> 'Search the regulations for personal data, top 2'
intent=search_vectors needs_input=False state=INTENT_CAPTURED
Top 2 passage(s) for 'personal data':
1. Retention Rules — Retention Rules > Art 1 (score 0.250): Art 1 Controllers must delete personal data after five years.
2. Retention Rules — Retention Rules (score -0.500): Retention Rules

>>> 'Generate eFLINT from the Retention Rules document'
intent=generate_eflint needs_input=False state=INTENT_CAPTURED
Generated an eFLINT specification from 'Retention Rules': 0 error(s), 4 warning(s), 1/1 scenarios passed. It is now the project's specification, ready for eFLINT verification.

>>> 'Generate eFLINT: Article 6(1)(a). Processing shall be lawful only if a'
intent=generate_eflint needs_input=False state=INTENT_CAPTURED
Generated an eFLINT specification from your text: 0 error(s), 0 warning(s), 1/1 scenarios passed. It is now the project's specification, ready for eFLINT verification.

>>> 'Validate the eFLINT specification'
intent=validate_eflint needs_input=False state=INTENT_CAPTURED
The project's eFLINT specification is valid: 0 error(s), 0 warning(s).
```

The project stayed in `INTENT_CAPTURED`, and these operations were recorded in its history:

```text
upload_document     Added document 'Retention Rules' (c4c6befa0358)
vectorize_document  Vectorized 'Retention Rules' into regs
generate_eflint     Generated eFLINT specification (succeeded, run 5bcf4a8eb835)
generate_eflint     Generated eFLINT specification (succeeded, run 8b7048766de9)
```

#### How similar requests are classified

Output of the heuristic classifier. The first four messages mention documents, regulations or eFLINT
but must not be taken for the new commands:

```text
'Verify the process against the eFLINT specification' -> verify_eflint {}
'Generate a Petri net and verify it against eFLINT' -> verify_eflint {}
'Upload the invoice to the portal and notify the manager' -> engineer_process {'process_description': 'Upload the invoice to the portal and notify the manager'}
'find similar project about regulation' -> find_project {'query': 'find similar project about regulation'}
'Search the regulations for compliance duties, top 3' -> search_vectors {'limit': 3, 'query': 'compliance duties'}
'vectorise document abc123def456 using the openai-large profile' -> vectorize_document {'profile': 'openai-large', 'document_ref': 'abc123def456'}
```

### Test results

```bash
uv run pytest tests -q --ignore=tests/eflint_verifier/test_live_service.py --ignore=tests/regulatory_docs/test_cloud_qdrant.py
```

```text
FAILED tests/text_to_bpmn/test_llm_providers.py::test_openai_provider_delegates_to_api
1 failed, 368 passed, 1 warning
```

- **New tests:** `tests/test_coordinator_capabilities.py` has 56 tests, all passing.
- **The failure isn't from this work:** `api/openai_client.py` imports `encode_image`, but
  `agents/text_to_bpmn/pipeline/content_provider.py` only defines `encode_image_with_type`. No files
  under `api/` or `agents/text_to_bpmn/` were changed.
- **The warning is new but harmless:** the 10 s manifest timeout triggers a Starlette deprecation
  warning when `HttpAgent` is tested through `TestClient`.
- **Cloud tests:** an earlier full run also included `test_cloud_qdrant.py` and gave 370 passed, 1
  failed. Those tests connect to the real cluster using `.env` credentials and write to a throwaway
  `regdocs_it_*` collection, which they try to delete afterwards. Leave them out with the
  `--ignore` above to stay offline.

**Not tested:**

- LLM routing with a real model (scripted responses only)
- vectorizing and searching through the coordinator against real Qdrant or OpenAI embeddings
- `eflint_verifier` against the live service
- the new eFLINT download button in a browser

### Known issues / next steps

- **Fix the `encode_image` import first.** From reading the code (not run): if the classifier uses the
  OpenAI provider, the failed import is reported as "openai package is not installed", and
  `LLMIntentClassifier.classify` doesn't catch it, so chat messages would likely fail.
- **Draft qualification rules.** They are still the one verification input that can't be generated, so
  chat can't yet go from regulation to verdict on its own.
- **No "the last document" reference.** A document must be named by id or title, unless the library
  holds only one.
- **Generated specs can overwrite earlier ones.** A spec that passes replaces the project's stored
  spec without asking, even if it has warnings. The reply says it was replaced by the new one but
  keeps no earlier version.
- **The keyword classifier has limits.** It covers common wordings only; unusual phrasing depends on
  the LLM classifier.
