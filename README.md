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

Each agent owns its full source under its own folder in `agents/`; only the transport-agnostic
contract is shared:

- **`agents/text_to_bpmn/`** — the text→BPMN agent, self-contained. Its `pipeline/` subpackage is the guideline→BPMN compiler (staged, provider-agnostic); `agent.py`/`service.py` expose it via the contract.
- **`agents/text_to_eflint/`** — the text→eFLINT agent (legal/normative text). Vendors the FLINT ontology + SHACL shapes (`flint/`) and an LLM-based semantic-role **labelling** module that turns legal sentences into FLINT act frames (ported from TNO's FlintFiller-SRL, provider-agnostic).
- **`agents/coordinator/`** — the coordinator agent, self-contained (intent, state machine, projects, cache, chat UI).
- **`agents/contract/`** — the shared contract + registry every agent implements.
- **`api/`** — the single place that talks to the real provider SDKs (Anthropic, OpenAI, Qdrant); the provider-agnostic LLM layer delegates its network calls here.

```
                        ┌──────────────────────────────────────────────┐
        user  ───────▶  │            COORDINATOR AGENT  (:8000)         │
   (NL request)         │  1. recognize intent (structured LLM/heuristic)│
                        │  2. resolve project: new | open | find-similar │
                        │       └─ SemanticProjectCache (no-op today)    │
                        │  3. gate the action via the state machine      │
                        │  4. route to a sub-agent via AgentRegistry     │
                        │  5. apply transition, record history, persist  │
                        └───────┬───────────────────────┬───────────────┘
              in-process        │                        │   HTTP (REST contract)
              adapter           │                        │
                                ▼                        ▼
                   ┌───────────────────────┐   ┌───────────────────────────┐
                   │  TEXT→BPMN AGENT       │   │  TEXT→eFLINT (foundation)  │
                   │  wraps its pipeline    │   │  FLINT frames + ontology   │
                   │  (:8100 when external) │   │  /SHACL — not yet routed   │
                   └───────────┬───────────┘   └───────────┬───────────────┘
                               │        ┌──────────────────┴───────────────┐
                               └───────▶│  api/  — real provider SDK calls  │
                                        │  (Anthropic · OpenAI · Qdrant)    │
                                        └───────────────────────────────────┘

  Persistence: ProjectStore (JSON on disk) + runs/ pipeline artifacts (referenced, not copied)
```

> The text→eFLINT agent currently ships its building blocks (ontology loader + LLM labelling) and is
> **not yet registered** in `config/agents.json`, so the coordinator does not route to it today.

#### A. Coordinator Agent
- **Intent Recognition:** Classifies user requests (heuristic offline; structured-LLM in production).
- **Project Resolution & Persistence:** Creates, opens, lists, and saves user projects across sessions.
- **State-Gated Actions:** A request whose lifecycle action the project's current state forbids is *refused*, not executed.
- **Semantic Cache (seam):** Interface + no-op default now; Qdrant-backed similarity search to detect duplicate/similar projects is provisioned for later.
- **Agent Registry:** Routes intents to specialized agents (in-process or external via HTTP) from `config/agents.json`.

#### B. The Text-to-BPMN Pipeline (Current Core, wrapped as an agent)
The 5-stage pipeline (now in `agents/text_to_bpmn/pipeline/`) is exposed as the `text_to_bpmn` sub-agent:
1.  **Ingestion & Evidence Extraction:** Extracts atomic, source-anchored statements from PDF/Markdown.
2.  **Intermediate Representation (IR):** A Pydantic model that enforces process logic (actors, elements, relations, thresholds).
3.  **Terminology Grounding:** Maps clinical terms to controlled vocabularies (SNOMED/LOINC) without altering source wording.
4.  **Deterministic Compilation:** Renders IR into schema-valid BPMN 2.0 (with auto-layout) and DMN 1.3.
5.  **Validation:** Bundled OMG XSD validation plus graph connectivity checks.

#### C. The Text-to-eFLINT Agent (legal / normative text)
Turns natural-language legislation into **FLINT act frames** and validates them against a formal ontology:
- **Vendored FLINT ontology (`flint/`):** the FLINT ontology + SHACL shapes + competency-question SPARQL queries, pinned to `flint-ontology v1.0.0` and shipped as package data. A small loader exposes the graph, shapes, queries, and a full pySHACL validation round-trip (needs the `eflint` extra: `rdflib` + `pyshacl`).
- **LLM-based labelling (`labelling/`):** a provider-agnostic port of the *Generative-LLM* approach from TNO's FlintFiller-SRL. It keeps the reusable IP — the semantic-role schema, the gold few-shot examples (EN/NL), the FLINT frame templates, and the roles→frame mapping — and drops the OpenAI-specific transport, BERT fine-tuning, and evaluation scaffolding. The LLM call reuses the same `LLMProvider.generate_json` interface as the BPMN pipeline, so any model works unchanged.

#### D. LLM Layer (`api/` + `agents/text_to_bpmn/pipeline/llm/`)
- **`api/`** owns the actual SDK calls: `ask_anthropic_structured` / `ask_openai_structured` force a single tool/function call to return schema-valid JSON, alongside the existing chat/tool-loop/vision helpers.
- The pipeline's `AnthropicProvider` / `OpenAIProvider` are **thin adapters** that delegate to `api/` and normalize results (`LLMResult`) and errors (`LLMError`); a missing SDK surfaces as a clean `LLMError`.
- `ScriptedLLMProvider` (mock) is the offline path used by the whole test suite — no keys, no network.
- Selection is config-driven: `LLM_PROVIDER=auto` (default) picks a provider from whichever API key is set; agents accept an injected `provider` or a `provider_factory` callable.

---

### 3. Project Structure

```
agents/                         # Agent layer — one folder per agent, shared contract
  contract/                     # shared contract: models, Agent/InProcessAgent/HttpAgent, registry
  paths.py                      # env-overridable config locations (agent-agnostic)
  storage.py                    # resolve per-agent/shared storage from config/storage.json
  coordinator/                  # ── COORDINATOR AGENT (self-contained) ──
    intent.py                   #   intent recognition (heuristic + structured-LLM)
    coordinator.py              #   orchestration: intent → project → state-gate → route → persist
    factory.py / service.py     #   wiring + FastAPI app (:8000)
    projects/                   #   Project model + pluggable ProjectStore (JSON on disk)
    state/                      #   data-driven lifecycle state machine (project-states.json)
    cache/                      #   SemanticProjectCache: no-op default + Qdrant skeleton (deferred)
    static/                     #   chat UI (index.html)
    storage/                    #   PRIVATE storage: projects/ (gitignored; README tracked)
    PROGRESS.md                 #   refactor changelog
  text_to_bpmn/                 # ── TEXT→BPMN AGENT (self-contained) ──
    agent.py / shaping.py       #   contract adapter over the pipeline
    service.py                  #   standalone agent service (:8100)
    demo/                       #   demo FastAPI + static UI; /api/generate shims over the agent
      main.py                   #     uvicorn agents.text_to_bpmn.demo.main:app
      static/index.html         #     renders BPMN with bpmn-js
    pipeline/                   #   the compiler: ingestion, evidence, ir, terminology,
                                #     bpmn, dmn, llm, retrieval, validation, config
      llm/                      #     provider-agnostic LLM interface + adapters (delegate to api/) + mock
      config/terminology.json   #   clinical terminology config (bundled with the agent)
      schemas/                  #   bundled OMG BPMN 2.0 / DMN 1.3 XSDs (bundled with the agent)
    storage/                    #   PRIVATE storage: runs/ pipeline artifacts (gitignored)
  text_to_eflint/               # ── TEXT→eFLINT AGENT (legal/normative text) ──
    flint/                      #   vendored FLINT ontology + SHACL shapes + SPARQL CQs + loader (package data)
    labelling/                  #   LLM semantic-role labelling → FLINT act frames (schema, few-shot, frames)

api/                            # single source of truth for real provider SDK calls
  anthropic_client.py           #   Anthropic SDK: structured JSON, chat, tool-loop, vision
  openai_client.py              #   OpenAI SDK: structured JSON, chat, tool-loop, vision
  qdrant_remote_client.py       #   Qdrant client (retrieval)

config/
  agents.json                   # agent registry (which agents, transport, base_url)
  project-states.json           # canonical project lifecycle spec
  storage.json                  # per-agent private + shared storage locations (swap for cloud later)
storage/                        # SHARED cross-agent exchange area (gitignored; README tracked)
fixtures/                       # sample process descriptions for the test suite (tracked)
tests/                          # offline test suite
todo/                           # design docs: COORDINATOR_PLAN.md, PROJECT_STATE_MANAGEMENT.md, …
```

---

### 4. Project Lifecycle (State Machine)

Projects move through a finite state machine defined in `config/project-states.json` (see `todo/PROJECT_STATE_MANAGEMENT.md`):

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
```

Configure providers (optional — everything runs offline without them) via a `.env` file:

```
ANTHROPIC_API_KEY=...     # or OPENAI_API_KEY=...
# LLM_PROVIDER=anthropic  # force a provider
```

---

### 6. How to Run

**Coordinator (recommended entry point)** — runs with the text→BPMN agent embedded in-process:

```bash
uvicorn agents.coordinator.service:app --port 8000
```

Then open **http://localhost:8000/** for the **chat UI**: a chat window to submit intent in natural
language, a sidebar listing your projects with their lifecycle state, and buttons to select, delete,
or start a new project. Generated BPMN models render inline (bpmn-js) with validation badges and a
download button; the active project's `allowed_actions` show as clickable chips.

Or talk to the API directly:

```bash
curl -s localhost:8000/coordinator/message -H 'content-type: application/json' -d '{
  "text": "The family physician assesses the IPSS. If improvement is at least 25% continue treatment, otherwise refer back to the urologist.",
  "user_id": "you@example.com"
}'
# → { intent, project:{id,...}, state:"MODEL_IN_PROGRESS", allowed_actions:[...], agent_result:{output:{bpmn_xml,...}} }

curl -s 'localhost:8000/coordinator/projects?user_id=you@example.com'
curl -s 'localhost:8000/coordinator/projects/<id>?user_id=you@example.com'
```

**Run a sub-agent as an external HTTP service** — start it on its own port, then point the registry at it (no coordinator code changes):

```bash
uvicorn agents.text_to_bpmn.service:app --port 8100
```

Edit `config/agents.json` and restart the coordinator:

```json
{ "name": "text_to_bpmn", "transport": "http", "base_url": "http://localhost:8100", "intents": ["engineer_process"] }
```

The coordinator now reaches the agent over the REST contract (`/agent/manifest`, `/agent/invoke_sync`, `/agent/invoke` + `/agent/jobs/{id}`, `/health`).

**Legacy demo UI** — the original single-shot demo still works (now a thin shim over the agent):

```bash
uvicorn agents.text_to_bpmn.demo.main:app --port 8080   # open http://localhost:8080 — renders BPMN with bpmn-js
```

**Configuration knobs (env vars):**

| Var | Default | Purpose |
| --- | --- | --- |
| `AGENTS_CONFIG` | `config/agents.json` | agent registry |
| `PROJECT_STATES_CONFIG` | `config/project-states.json` | lifecycle spec |
| `PROJECTS_DIR` | `./projects` | project store location |
| `RUNS_DIR` | `./runs` | pipeline artifacts |
| `SEMANTIC_CACHE` | `noop` | `noop` \| `qdrant` (qdrant deferred) |
| `COORDINATOR_PORT` / `TEXT_TO_BPMN_PORT` | `8000` / `8100` | service ports |
| `LLM_PROVIDER` | `auto` | `auto` (pick by available key) \| `anthropic` \| `openai` |
| `ANTHROPIC_MODEL` / `OPENAI_MODEL` | `claude-sonnet-4-6` / `gpt-4o` | override the model per provider |

---

### 7. Testing

```bash
pytest -q      # 104 tests, fully offline (scripted LLM + heuristic classifier); no API keys required
```

The whole suite runs without keys or network: real LLM calls are exercised by monkeypatching the
`api/` layer, and the text→eFLINT labelling tests use the scripted provider. Tests that need an
optional extra self-skip when it is absent (e.g. the eFLINT ontology tests without `.[eflint]`, or
the OpenAI happy-path without `.[llm]`).

---

### 8. Current Status & Supported Features

**Currently Supported & Tested:**
- ✅ **Coordinator Agent:** Intent recognition, project management, state-gated routing to sub-agents.
- ✅ **Chat UI:** Web front end at `/` — chat window, project sidebar (select / delete / new), inline BPMN rendering.
- ✅ **Agent Contract & Registry:** One contract, in-process **and** HTTP transports; agents are hot-swappable via config.
- ✅ **Project Lifecycle State Machine:** Data-driven from `config/project-states.json`; constrains which actions apply.
- ✅ **BPMN 2.0 & DMN 1.3 Generation:** Verified against official OMG schemas.
- ✅ **Source Anchoring:** Rejects any model element not backed by extracted evidence.
- ✅ **Multi-Format Ingestion:** Markdown and PDF with section-aware chunking.
- ✅ **Offline Verification:** Comprehensive test suite (104 tests) runs fully offline using scripted LLM mocks.
- ✅ **DMN Logic:** Deterministic generation of FEEL rules from textual conditions.
- ✅ **Centralized, Pluggable LLM Access:** One `api/` package for all provider SDK calls; thin provider adapters; config-driven `auto`/`anthropic`/`openai` selection with an injectable per-agent LLM interface.
- ✅ **Text→eFLINT Agent (foundation):** Vendored FLINT ontology + SHACL validation, and provider-agnostic LLM labelling of legal text into FLINT act frames (semantic-role schema, EN/NL few-shot examples, roles→frame mapping).

**Provisioned / Future:**
- **Semantic Cache:** `QdrantSemanticCache` (embed project descriptions; find similar/existing projects) — seam shipped, implementation deferred.
- **External Sub-Agents:** Verification/critic, simulation (SpiffWorkflow), knowledge-graph — behind the same contract. `verify_model` is already recognized and state-gated; it returns "agent not available" until one is wired.
- **Text→eFLINT — full frames:** current labelling fills `acts`; extracting `preconditions`/conditions, populating `facts`/`duties`, filling `explanation`, and wiring the labeller to the ontology validator are the next steps.
- **Executable Workflow Generation:** Direct-execution formats (e.g., SpiffWorkflow).
- **Auth & Storage:** Real authentication for `user_id`; optional DB-backed `ProjectStore`.

See `todo/COORDINATOR_PLAN.md` and `agents/coordinator/PROGRESS.md` for the full design and changelog.
