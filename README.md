# Agentic Workflow Planner

The Agentic Workflow Planner serves as a **verified process compiler with an LLM semantic front-end**. Unlike traditional chatbots that might "hallucinate" process diagrams, this system treats natural language as a source for structured evidence, which is then deterministically compiled into industry-standard models.

**Core Principles:**
- **Evidence over Memory:** Elements must be anchored to source text.
- **Deterministic Compilation:** No direct LLM-to-XML path; structure is enforced by code.
- **Auditability:** Every task or decision in the resulting diagram must be traceable back to the specific line or page in the source document.
- **Verification-First:** Automatic syntax, graph, and semantic validation are mandatory before a workflow is considered "verified."
- **Code-Orchestrated Delegation:** The LLM only *classifies intent and extracts slots* (structured output); Python decides control flow. Routing is never free prose.
- **Transport-Agnostic Agents:** Every sub-agent implements one contract and can run in-process **or** as an external HTTP service — switching is a config change, not a code change.
- **Pluggable, Centralized LLM Access:** Every model call goes through one provider-agnostic interface (`LLMProvider.generate_json`, structured output only). The real SDK calls live in a single `api/` package; providers (Anthropic/OpenAI) are thin adapters over it, and each agent is *configured* with its LLM interface (a concrete provider or a factory), so models are swappable without touching agent/pipeline code.

---

### 2. Architecture

Each agent owns its full source under its own folder in `agents/`, and each folder has a README with
the detailed description. Only the transport-agnostic contract is shared:

| Agent | What it does | Intents | Details |
| --- | --- | --- | --- |
| **coordinator** | The natural-language entry point: intent recognition, projects, lifecycle state machine, routing, chat UI. | — | [`agents/coordinator/`](agents/coordinator/README.md) |
| **text_to_bpmn** | Process text → validated BPMN 2.0 (+ DMN 1.3) through a staged, evidence-anchored pipeline. | `engineer_process` | [`agents/text_to_bpmn/`](agents/text_to_bpmn/README.md) |
| **petri_net_generator** | BPMN 2.0 XML / text→BPMN IR / text → ordinary workflow Petri net → PNML, with structural and soundness checks. | `generate_petri_net` | [`agents/petri_net_generator/`](agents/petri_net_generator/README.md) |
| **eflint_verifier** | Verifies a Petri net against an eFLINT specification through qualification rules (bounded model checking, compliance checking, planning). A wrapper around the **external** eFLINT model-checking service. | `verify_eflint` | [`agents/eflint_verifier/`](agents/eflint_verifier/README.md) |
| **text_to_eflint** | Regulatory text → executable eFLINT specification (LLM draft, then parse / type-check / scenario-test / repair). Also vendors the FLINT ontology and an LLM semantic-role labeller. | `generate_eflint`, `validate_eflint` | [`agents/text_to_eflint/`](agents/text_to_eflint/README.md) |
| **regulatory_docs** | Content-addressed library of regulatory documents (upload / find) and config-driven vectorization into Qdrant. | `upload_document`, `find_documents`, `vectorize_document` | [`agents/regulatory_docs/`](agents/regulatory_docs/README.md) |

- **`agents/contract/`**: the shared contract (`AgentHandler`, `InProcessAgent`, `HttpAgent`,
  request/result models) and the `AgentRegistry` that reads `config/agents.json`.
- **`api/`**: the single place that talks to the real provider SDKs (Anthropic, OpenAI, Qdrant);
  the provider-agnostic LLM layer delegates its network calls here.

```
               user: NL request, pasted BPMN or eFLINT verification inputs
                                                  │
                                                  ▼
                      ┌──────────────────────────────────────────────────────┐
                      │              COORDINATOR AGENT  (:8000)              │
                      │ 1. recognize intent (structured LLM / heuristic)     │
                      │ 2. resolve project: new | open | find-similar        │
                      │ 3. gate the action via the state machine             │
                      │ 4. route to a sub-agent via AgentRegistry            │
                      │ 5. apply transition, record history, persist         │
                      └───────────────────────────┬──────────────────────────┘
                                                  │
                in-process adapter, or HTTP (REST contract) — per config/agents.json
       ┌────────────────┬────────────────┬────────┴───────┬────────────────┬────────────────┐
       ▼                ▼                ▼                ▼                ▼                ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ TEXT→BPMN   │  │ PETRI NET   │  │ eFLINT      │  │ TEXT→eFLINT │  │ REGULATORY  │  │ (future:    │
│ :8120       │  │ GENERATOR   │  │ VERIFIER    │  │ :8091       │  │ DOCS :8110  │  │ simulation, │
│             │  │ :8200       │  │ :8300       │  │ not routed  │  │ not routed  │  │ KG, …)      │
└──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └─────────────┘
       ▼                ▼                ▼                ▼                ▼
 LLM providers     PM4Py (AGPL)    eFLINT model-    LLM providers     embeddings,
                                    checking svc                         Qdrant
                                    :8100 (Rust)

  Persistence: ProjectStore (JSON on disk) + per-agent storage/runs/ artifacts (referenced, not copied)
  Hand-off:    storage/projects/<id>/ holds each project's model.bpmn, model.pnml and eFLINT
               verification inputs (spec.eflint, rules.qr, checks.analysis) for downstream agents
```

Ports are those of each agent's standalone service; by default every sub-agent runs **in-process**
inside the coordinator. The coordinator routes `engineer_process`, `generate_petri_net` and
`verify_eflint` (also reached through `verify_model` when a project has an eFLINT spec). The
`text_to_eflint` and `regulatory_docs` agents are registered, but the intent classifier does not
emit their intents yet; use their demo apps or call them directly.

#### LLM Layer (`api/` + `agents/text_to_bpmn/pipeline/llm/`)
- **`api/`** owns the actual SDK calls: `ask_anthropic_structured` / `ask_openai_structured` force a single tool/function call to return schema-valid JSON, alongside the existing chat/tool-loop/vision helpers.
- The pipeline's `AnthropicProvider` / `OpenAIProvider` are **thin adapters** that delegate to `api/` and normalize results (`LLMResult`) and errors (`LLMError`); a missing SDK surfaces as a clean `LLMError`.
- `ScriptedLLMProvider` (mock) is the offline path used by the whole test suite — no keys, no network.
- Selection is config-driven: `LLM_PROVIDER=auto` (default) picks a provider from whichever API key is set; agents accept an injected `provider` or a `provider_factory` callable.

---

### 3. Project Structure

```
agents/                         # Agent layer — one folder per agent (each with README.md), shared contract
  contract/                     #   shared contract: models, Agent/InProcessAgent/HttpAgent, registry
  paths.py                      #   env-overridable config locations (agent-agnostic)
  storage.py                    #   resolve per-agent/shared storage from config/storage.json
  coordinator/                  #   COORDINATOR — intent, orchestration, projects, state machine, chat UI
  text_to_bpmn/                 #   TEXT→BPMN — pipeline/ (the compiler), agent, service, demo UI
  petri_net_generator/          #   PETRI NET GENERATOR — BPMN/IR/text → workflow net → PNML
  eflint_verifier/              #   eFLINT VERIFIER — client of the external eFLINT model-checking service
  text_to_eflint/               #   TEXT→eFLINT — generation/, labelling/, ontology/, service, demo UI
  regulatory_docs/              #   REGULATORY DOCS — document library, embeddings, Qdrant vectorizer, demo UI
  <agent>/storage/              #   each agent's PRIVATE storage (gitignored; README tracked)
  <agent>/pyproject.toml        #   standalone project selecting the agent's extra (see RUN.md, Option B)

api/                            # single source of truth for real provider SDK calls
  anthropic_client.py           #   Anthropic SDK: structured JSON, chat, tool-loop, vision
  openai_client.py              #   OpenAI SDK: structured JSON, chat, tool-loop, vision
  qdrant_remote_client.py       #   Qdrant client (retrieval)

config/
  agents.json                   # agent registry (which agents, transport, base_url, intents)
  project-states.json           # canonical project lifecycle spec
  storage.json                  # per-agent private + shared storage locations (swap for cloud later)
  vectorization.json            # regulatory_docs vectorization profiles (embedding model, chunking, Qdrant target)
storage/                        # SHARED cross-agent exchange area (gitignored; README tracked)
tests/                          # offline test suite, one folder per agent (text→BPMN sample guidelines in tests/text_to_bpmn/fixtures/)
todo/                           # design docs + changelogs, one folder per agent (IMPLEMENTATION_PLAN.md, PROGRESS.md, …)
```

---

### 4. Project Lifecycle (State Machine)

Projects move through a finite state machine defined in `config/project-states.json` (see `todo/coordinator/PROJECT_STATE_MANAGEMENT.md`):

```
INTENT_CAPTURED → DESCRIPTION_DRAFTED → MODEL_IN_PROGRESS → MODEL_READY_FOR_VERIFICATION
  → VERIFICATION_IN_PROGRESS → VERIFIED → IMPLEMENTATION_GENERATED → TESTING
  → READY_FOR_DEVELOPMENT → DEPLOYED → RETIRED
```

Two global actions apply from any state:
- **`deleteProject`** — terminal.
- **`reviseDescription`** — stays in place if the edit doesn't change workflow semantics; otherwise returns to `MODEL_IN_PROGRESS` and **invalidates** prior verification/approvals.

Every coordinator response reports the current `state` and its `allowed_actions`, so the client always knows what is permitted next.

---

### 5. Installation

Requires Python ≥ 3.11.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev,bpmn]        # bpmn extra pulls lxml/xmlschema for compilation + validation
# optional: .[llm] for Anthropic/OpenAI providers, .[retrieval] for Qdrant,
#           .[eflint] for the text→eFLINT ontology loader (rdflib + pyshacl)
#           .[petri_net] for the Petri net generator (pm4py — AGPL-3.0)
# per-agent extras: .[text_to_bpmn] .[text_to_eflint] .[coordinator] .[regulatory_docs]
#                   .[petri_net_generator] .[eflint_verifier]; .[all] for everything
```

With `uv`: `uv sync --extra all` (add `--extra dev` for tests). Each agent can also be installed in
its own isolated env from its folder (`cd agents/regulatory_docs && uv sync`) — see `RUN.md`.

Configure providers (optional — everything runs offline without them) via a `.env` file:

```
ANTHROPIC_API_KEY=...     # or OPENAI_API_KEY=...
# LLM_PROVIDER=anthropic  # force a provider
QDRANT_URL=...            # cloud Qdrant 'main' cluster (regulatory_docs vectorization)
QDRANT_API_KEY=...
# QDRANT_URL_KG=... / QDRANT_API_KEY_KG=...   # optional 'kg' cluster
# EFLINT_VERIFIER_URL=http://127.0.0.1:8100   # external eFLINT model-checking service
```

**eFLINT verification** additionally needs the external eFLINT model-checking webservice
(`eflint-model-checking`, Rust + Clingo) running separately:

```bash
cd <path-to>/eflint-model-checking && cargo run -p eflint-webservice   # listens on http://127.0.0.1:8100
```

---

### 6. How to Run

**Coordinator (recommended entry point)** — runs every registered sub-agent in-process:

```bash
uvicorn agents.coordinator.service:app --port 8000
```

Open **http://localhost:8000/** for the **chat UI**: a chat window, a project sidebar with
lifecycle states, inline BPMN rendering, Petri net downloads, and the active project's
`allowed_actions` as chips. Things to try:

- a process description → BPMN model (`engineer_process`);
- *"Translate the model into a Petri net"*, or *"Petri net for: …"* (`generate_petri_net`);
- *"Verify the process against the eFLINT specification"* with ```` ```eflint ```` and
  ```` ```qualification ```` blocks pasted into the message (`verify_eflint`, needs the external
  service).

The HTTP API (`POST /coordinator/message`, `GET /coordinator/projects`, …) is described in
[`agents/coordinator/README.md`](agents/coordinator/README.md).

**Sub-agents on their own** — each has a standalone service or demo UI:

| Agent | Command | Port |
| --- | --- | --- |
| text_to_bpmn | `uvicorn agents.text_to_bpmn.service:app` (contract) / `agents.text_to_bpmn.demo.main:app` (demo UI) | 8120 / 8080 |
| petri_net_generator | `uvicorn agents.petri_net_generator.service:app` / `agents.petri_net_generator.demo.main:app` (viewer + token game) | 8200 / 8210 |
| eflint_verifier | `uvicorn agents.eflint_verifier.service:app` (needs the external service on 8100) | 8300 |
| text_to_eflint | `uvicorn agents.text_to_eflint.service:app` / `agents.text_to_eflint.demo.main:app` (demo UI) | 8091 / 8090 |
| regulatory_docs | `uvicorn agents.regulatory_docs.demo.main:app` (demo UI) | 8110 |

To have the coordinator reach a sub-agent over HTTP instead of in-process, edit its entry in
`config/agents.json` and restart the coordinator (no code changes):

```json
{ "name": "text_to_bpmn", "transport": "http", "base_url": "http://localhost:8120", "intents": ["engineer_process"] }
```

The coordinator then uses the REST contract (`/agent/manifest`, `/agent/invoke_sync`,
`/agent/invoke` + `/agent/jobs/{id}`, `/health`). See [`RUN.md`](RUN.md) for install options and
request examples, and each agent's README for its endpoints and settings.

**Shared configuration (env vars):**

| Var | Default | Purpose |
| --- | --- | --- |
| `AGENTS_CONFIG` | `config/agents.json` | agent registry |
| `PROJECT_STATES_CONFIG` | `config/project-states.json` | lifecycle spec |
| `STORAGE_CONFIG` | `config/storage.json` | per-agent / shared storage locations |
| `HANDOFF_DIR` | `storage/projects` | shared per-project artifacts (BPMN, PNML, eFLINT verification inputs) |
| `LLM_PROVIDER` | `auto` | `auto` (pick by available key) \| `anthropic` \| `openai` |
| `ANTHROPIC_MODEL` / `OPENAI_MODEL` | `claude-sonnet-4-6` / `gpt-4o` | override the model per provider |

Agent-specific settings (`PROJECTS_DIR`, `RUNS_DIR`, `PETRI_NET_*`, `EFLINT_VERIFIER_*`,
`EFLINT_RUNS_DIR`, `REGDOCS_DIR`, `VECTORIZATION_CONFIG`, ports, …) are listed in the agent READMEs.

---

### 7. Testing

```bash
pytest -q                                   # 273 tests, offline by default (scripted LLM + heuristic classifier)
pytest tests/<agent> -q                     # one agent: coordinator, text_to_bpmn, petri_net_generator,
                                            #   eflint_verifier, text_to_eflint, regulatory_docs
pytest tests/test_coordinator_eflint.py -q  # coordinator ↔ eFLINT verifier routing
```

The whole suite runs without keys or network: real LLM calls are exercised by monkeypatching the
`api/` layer, the agents use the scripted provider, and the eFLINT verifier runs against a fake of
the external service's job API. Tests that need an optional extra skip themselves when it is
absent (e.g. PM4Py for the Petri net generator). Two groups of tests talk to live services and are
opt-in:

- `tests/regulatory_docs/test_cloud_qdrant.py` writes to a throwaway collection on the live cloud
  Qdrant (and deletes it) when `QDRANT_URL` / `QDRANT_API_KEY` are set;
- `tests/eflint_verifier/test_live_service.py` runs against the real eFLINT model-checking service
  when `EFLINT_VERIFIER_LIVE=1` (add `EFLINT_VERIFIER_KEEP_RUNS=1` to keep its run records in the
  agent's storage).

`pytest` here means the project environment's pytest (activated `.venv`, or `uv run pytest`). A
system `pytest` lacks the dependencies.

---

### 8. Current Status & Supported Features

**Currently Supported & Tested:**
- ✅ **Coordinator Agent:** Intent recognition, project management, state-gated routing to sub-agents, chat UI.
- ✅ **Agent Contract & Registry:** One contract, in-process **and** HTTP transports; agents are hot-swappable via config.
- ✅ **Project Lifecycle State Machine:** Data-driven from `config/project-states.json`; constrains which actions apply.
- ✅ **Text→BPMN Agent:** BPMN 2.0 & DMN 1.3 verified against the OMG schemas; source anchoring; Markdown/PDF ingestion; deterministic FEEL rules.
- ✅ **Centralized, Pluggable LLM Access:** One `api/` package for all provider SDK calls; thin provider adapters; config-driven `auto`/`anthropic`/`openai` selection.
- ✅ **Petri Net Generator Agent:** BPMN / text→BPMN IR / text → workflow Petri net → PNML (+DOT), with structural/workflow-net validation and a bounded soundness check.
- ✅ **eFLINT Verifier Agent:** Petri net + eFLINT + qualification rules → verdict with counterexample/plan witnesses, via the external eFLINT model-checking service; PNML normalisation; default compliance check; routed by the coordinator with lifecycle transitions.
- ✅ **Text→eFLINT Agent:** Regulatory text → executable eFLINT specification with check/repair loop and scenario simulation; FLINT ontology + SHACL; LLM semantic-role labelling.
- ✅ **Regulatory Docs Agent:** Document library (content-hash dedup) and provenance-rich, idempotent vectorization into cloud or in-process Qdrant.
- ✅ **Offline Verification:** The full test suite runs offline using scripted LLM mocks and service fakes.

**Provisioned / Future:**
- **Semantic Cache:** `QdrantSemanticCache` (find similar/existing projects) — seam shipped, implementation deferred.
- **eFLINT verification — fewer manual inputs:** draft qualification rules with an LLM and check them with the service's `validate` operation; feed `text_to_eflint` output into verification.
- **Coordinator routing** for the `text_to_eflint` and `regulatory_docs` intents.
- **Further sub-agents:** simulation (SpiffWorkflow), knowledge graph — behind the same contract.
- **Executable Workflow Generation:** Direct-execution formats (e.g., SpiffWorkflow).
- **Auth & Storage:** Real authentication for `user_id`; optional DB-backed `ProjectStore`.

Each agent README lists that agent's next steps. See `todo/<agent>/` (e.g.
`todo/coordinator/IMPLEMENTATION_PLAN.md`, `todo/coordinator/PROGRESS.md`) for the full design and
changelogs.
