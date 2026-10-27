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
- **`agents/text_to_eflint/`** — the text→eFLINT agent (legal/normative text). Vendors the FLINT ontology + SHACL shapes (`ontology/`) and an LLM-based semantic-role **labelling** module that turns legal sentences into FLINT act frames (ported from TNO's FlintFiller-SRL, provider-agnostic).
- **`agents/regulatory_docs/`** — the regulatory-documents agent: a content-addressed document library (upload / find) plus config-driven vectorization of a selected document into a (cloud) **Qdrant** collection.
- **`agents/petri_net_generator/`** — the Petri net generator agent: turns BPMN 2.0 XML (e.g. the text→BPMN agent's output), its graph IR, or natural-language text (via a small process-algebra IR) into an ordinary **workflow Petri net**, validated and exported as **PNML** for external analysis tools.
- **`agents/coordinator/`** — the coordinator agent, self-contained (intent, state machine, projects, cache, chat UI).
- **`agents/contract/`** — the shared contract + registry every agent implements.
- **`api/`** — the single place that talks to the real provider SDKs (Anthropic, OpenAI, Qdrant); the provider-agnostic LLM layer delegates its network calls here.

```
                          ┌──────────────────────────────────────────────────┐
        user  ───────▶    │            COORDINATOR AGENT  (:8000)            │
   (NL request / BPMN)    │ 1. recognize intent (structured LLM/heuristic)   │
                          │ 2. resolve project: new | open | find-similar    │
                          │      └─ SemanticProjectCache (no-op today)       │
                          │ 3. gate the action via the state machine         │
                          │ 4. route to a sub-agent via AgentRegistry        │
                          │ 5. apply transition, record history, persist     │
                          └────────────┬────────────────────────┬────────────┘
              in-process               │                        │   HTTP (REST contract)
              adapter                  │                        │
               ┌───────────────────────┴────────────────────────┴─────────────────────────┐
               ▼                        ▼                        ▼                        ▼
    ┌──────────────────────┐ ┌──────────────────────┐ ┌──────────────────────┐ ┌──────────────────────┐
    │ TEXT→BPMN AGENT      │ │ REGULATORY DOCS      │ │ PETRI NET GENERATOR  │ │ TEXT→eFLINT (found.) │
    │ wraps its pipeline   │ │ upload · find ·      │ │ BPMN/IR/text → PNML  │ │ FLINT frames+ontology│
    │ (:8100 when external)│ │ vectorize → Qdrant   │ │ (:8200 when external)│ │ /SHACL — not routed  │
    └──────────┬───────────┘ └──────────┬───────────┘ └──────────┬───────────┘ └──────────┬───────────┘
               ▼                        ▼                        ▼                        ▼
    ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
    │           LLM / embedding providers (Anthropic · OpenAI) · Qdrant · PM4Py (Petri nets)          │
    └─────────────────────────────────────────────────────────────────────────────────────────────────┘

  Persistence: ProjectStore (JSON on disk) + per-agent runs/ artifacts (referenced, not copied)
  Hand-off:    coordinator copies each project's BPMN model to shared storage/projects/<id>/ for
               downstream agents (e.g. BPMN → Petri net)
```

> The text→eFLINT agent currently ships its building blocks (ontology loader + LLM labelling) and is
> **not yet registered** in `config/agents.json`, so the coordinator does not route to it today.
> The regulatory-docs agent **is** registered (intents `upload_document`, `find_documents`,
> `vectorize_document`), but the coordinator's intent classifier does not emit those intents yet —
> use its demo app (or call the agent directly) for now.
> The Petri net generator agent is registered (intent `generate_petri_net`), wired in-process, and
> **routed by the coordinator** (see A and E below).

#### A. Coordinator Agent
- **Intent Recognition:** Classifies user requests (heuristic offline; structured-LLM in production).
- **Project Resolution & Persistence:** Creates, opens, lists, and saves user projects across sessions.
- **State-Gated Actions:** A request whose lifecycle action the project's current state forbids is *refused*, not executed.
- **Semantic Cache (seam):** Interface + no-op default now; Qdrant-backed similarity search to detect duplicate/similar projects is provisioned for later.
- **Agent Registry:** Routes intents to specialized agents (in-process or external via HTTP) from `config/agents.json`.
- **Petri Net Requests:** Messages mentioning a Petri net / workflow net / PNML are classified as `generate_petri_net` and forwarded to the Petri net generator. A BPMN 2.0 document pasted into the message is split off **in code** before classification and forwarded verbatim (never echoed through the LLM). The source is chosen in this order: attached BPMN → a process description in the message (e.g. `Petri net for: …`) → the active project's BPMN model → the project's description. It is a derived view, so it is recorded in the project history but does **not** change the lifecycle state. Blocking ambiguities come back as clarification questions.
- **Model Hand-off:** After the text→BPMN agent produces a model, the coordinator copies the BPMN XML to the shared exchange area (`storage/projects/<project_id>/model.bpmn`, `HANDOFF_DIR` overrides) so downstream agents can be given "the project's model"; it is removed when the project is deleted.

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

#### D. The Regulatory Docs Agent (document library + vectorization)
Lets a user **upload** regulatory documents, **find/select** one from a library, and **vectorize** the selected document into **Qdrant** for later retrieval:
- **Document library (`library.py`):** file-backed catalogue under the agent's private storage (`documents/<doc_id>/document.json` + `meta.json`). `doc_id` is a **content hash**, so re-uploading the same bytes is idempotent. Reuses the text→BPMN ingestion loaders, so Markdown / plain text / PDF are supported. Each record keeps its vectorization history.
- **Vectorization profiles (`config/vectorization.json`):** a named profile fixes the embedding backend (provider/model/dimension/batch size), chunking, and target Qdrant cluster/collection/distance. Shipped: **`default`** (OpenAI `text-embedding-3-small`, 1536-d → cloud `main`), **`openai-large`** (`text-embedding-3-large`, 3072-d → cloud `main`), **`offline`** (deterministic local hash embedder → in-process Qdrant; dev/tests only). Swapping a model or collection is a config edit.
- **Embedders (`embeddings.py`):** `openai` (real embeddings, needs `OPENAI_API_KEY`) or `hash` (dependency-free, deterministic, *not* semantically meaningful).
- **Qdrant store (`qdrant_store.py`):** clusters `main`/`kg` connect to **cloud Qdrant** using `QDRANT_URL`/`QDRANT_API_KEY` (resp. `QDRANT_URL_KG`/`QDRANT_API_KEY_KG`) read from `.env`; `memory` uses qdrant-client's in-process mode.
- **Vectorizer (`vectorizer.py`):** section-aware chunking → embed → ensure collection → upsert one point per chunk, with full **provenance** in the payload (doc id, chunk id, source, section, page, line range). Point ids are a stable `uuid5(doc_id, chunk_id)`, so re-vectorizing **overwrites rather than duplicates**.
- Embedder and store are **injected** (`embedder_factory` / `store_factory`), so the agent is fully testable offline.

#### E. The Petri Net Generator Agent (BPMN / text → workflow Petri net → PNML)
Produces an ordinary place/transition **workflow net** (unweighted arcs, one source place with the initial token, one sink place as the final marking) for external Petri-net tools. Four input types (`input_type`):
- **`bpmn_xml`** — BPMN 2.0 XML, e.g. `bpmn_xml` from the text→BPMN agent's output. Parsed with a hardened lxml parser (no DTDs / entity expansion / network), checked against an allow-list, then converted with **PM4Py**. Supported: start/end events, tasks (all task types), sequence flows, exclusive and parallel gateways, lanes/pools of a single process; inclusive gateways are converted with a warning (PM4Py heuristic, not verified). Anything else (sub-processes, boundary/intermediate events, event-based/complex gateways, message flows, loop markers, multiple processes) is **rejected with a structured error** instead of being silently dropped.
- **`text_to_bpmn_ir`** — the text→BPMN agent's graph IR (`runs/<run_id>/ir.json`), compiled to BPMN with that agent's compiler and then converted as above.
- **`text`** — natural language → one structured LLM call (`LLMProvider.generate_json`) producing a typed, versioned **process-algebra IR** (`activity` / `sequence` / `xor` / `parallel` / `loop`, plus `assumptions` and `ambiguities`). Blocking ambiguities return `status: "needs_clarification"` with questions for the user instead of a net.
- **`ir`** — a process-algebra IR supplied directly (e.g. after clarification/editing); compiled **deterministically** (`compiler.py`), never by the LLM.

Every net goes through common validation (`net_validation.py`): unique IDs, no dangling/weighted arcs, well-defined markings, single source/sink, every node on a source→sink path, and an optional bounded **soundness** check (option to complete, proper completion, no dead transitions, boundedness) that reports `passed` / `failed` / `not_checked` — `passed` only when the full state space was explored within the budget. Output: PNML (deterministic IDs; PM4Py `finalmarkings` extension; round-trips through PM4Py), optional DOT preview, a net summary, markings, findings, and the IR. PNML is also stored under the agent's private storage (`runs/<run_id>/`).

> PM4Py is licensed **AGPL-3.0**; it is only pulled in by the `petri_net` extra.

#### F. LLM Layer (`api/` + `agents/text_to_bpmn/pipeline/llm/`)
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
    demo/                       #   demo FastAPI + static UI: colour-coded role labelling + legend
      main.py                   #     uvicorn agents.text_to_eflint.demo.main:app  (POST /api/label)
      static/index.html         #     per-role coloured tokens, legend, act-frame view
  petri_net_generator/          # ── PETRI NET GENERATOR AGENT (BPMN / IR / text → workflow net → PNML) ──
    agent.py / service.py       #   PetriNetGeneratorHandler (generate_petri_net) + FastAPI service (:8200, POST /generate)
    generator.py                #   orchestration of the four input paths + validation + export
    ir.py / text_to_ir.py       #   process-algebra IR (Pydantic) + LLM text→IR extraction
    compiler.py                 #   deterministic IR → PM4Py Petri net
    bpmn_converter.py           #   secure BPMN parse + allow-list scan + PM4Py conversion
    net_validation.py           #   structural / workflow-net checks, bounded soundness, trace enumeration
    exporters.py                #   PNML (stable IDs) + DOT
    storage/                    #   PRIVATE storage: runs/ (gitignored; README tracked)
  regulatory_docs/              # ── REGULATORY DOCS AGENT (document library + Qdrant vectorization) ──
    agent.py                    #   RegulatoryDocsHandler: upload_document / find_documents / vectorize_document
    library.py                  #   content-addressed document library (Markdown / txt / PDF)
    config.py                   #   vectorization profiles (loads config/vectorization.json)
    embeddings.py               #   embedders: openai | hash (offline)
    qdrant_store.py             #   VectorStore protocol + Qdrant store (cloud main/kg | in-process memory)
    vectorizer.py               #   chunk → embed → upsert with provenance; idempotent point ids
    demo/                       #   demo FastAPI + static UI: upload, search/select, pick profile, vectorize
    storage/                    #   PRIVATE storage: documents/ (gitignored; README tracked)

api/                            # single source of truth for real provider SDK calls
  anthropic_client.py           #   Anthropic SDK: structured JSON, chat, tool-loop, vision
  openai_client.py              #   OpenAI SDK: structured JSON, chat, tool-loop, vision
  qdrant_remote_client.py       #   Qdrant client (retrieval)

config/
  agents.json                   # agent registry (which agents, transport, base_url)
  project-states.json           # canonical project lifecycle spec
  storage.json                  # per-agent private + shared storage locations (swap for cloud later)
  vectorization.json            # regulatory_docs vectorization profiles (embedding model, chunking, Qdrant target)
storage/                        # SHARED cross-agent exchange area (gitignored; README tracked)
fixtures/                       # sample process descriptions for the test suite (tracked)
tests/                          # offline test suite
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
# per-agent extras: .[text_to_bpmn] .[text_to_eflint] .[coordinator] .[regulatory_docs] .[petri_net_generator]; .[all] for everything
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

**Text→eFLINT labelling demo** — paste/upload legal text and see each sentence tagged with FLINT semantic roles (colour-coded, with a legend) plus the derived act frames:

```bash
uvicorn agents.text_to_eflint.demo.main:app --port 8090   # open http://localhost:8090
```

Needs an LLM key (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`), since labelling is one structured LLM call per sentence. Language: English or Dutch.

**Regulatory docs demo** — paste or upload (Markdown / txt / PDF) regulatory documents, search and select one, choose a vectorization profile, and push it to Qdrant:

```bash
uvicorn agents.regulatory_docs.demo.main:app --port 8100   # open http://localhost:8100
```

API: `POST /api/upload`, `POST /api/upload_file`, `GET /api/documents?query=`, `GET /api/profiles`, `POST /api/vectorize` (`{doc_id, profile}`), `GET /api/health`.
The `default`/`openai-large` profiles need `OPENAI_API_KEY` + `QDRANT_URL`/`QDRANT_API_KEY`; the `offline` profile needs nothing.
(Port 8100 is also the default for the external text→BPMN service — pick another port if you run both.)

**Petri nets from the chat** — e.g. *"Translate the model into a Petri net"* (with a project open),
*"Petri net for: A request is submitted and validated…"*, or paste a BPMN document after
*"Convert this to a Petri net:"*. The reply shows workflow-net / soundness badges and a **Download .pnml** button.

**Petri net generator service** — BPMN, IR or text in; workflow net + PNML out:

```bash
uvicorn agents.petri_net_generator.service:app --port 8200   # or PETRI_NET_PORT
curl -s localhost:8200/generate -H 'content-type: application/json' -d '{
  "input_type": "bpmn_xml", "bpmn_xml": "<?xml version=\"1.0\"?><bpmn:definitions ...>...</bpmn:definitions>",
  "output_formats": ["pnml", "dot"]
}'
# → { status, process_name, net_summary, initial_marking, final_marking,
#     validation:{structurally_valid, workflow_net, soundness, findings}, warnings, pnml, pnml_ref, dot }
```

`input_type` is one of `bpmn_xml`, `text_to_bpmn_ir`, `text`, `ir` (field of the same name carries the input).
It also serves the standard contract (`/agent/invoke_sync` with intent `generate_petri_net`, `/agent/manifest`, `/health`);
to reach it from the coordinator over HTTP, set `transport: http, base_url: http://localhost:8200` for
`petri_net_generator` in `config/agents.json`. `status: "failed"` responses use HTTP 422.

**Configuration knobs (env vars):**

| Var | Default | Purpose |
| --- | --- | --- |
| `AGENTS_CONFIG` | `config/agents.json` | agent registry |
| `PROJECT_STATES_CONFIG` | `config/project-states.json` | lifecycle spec |
| `PROJECTS_DIR` | `./projects` | project store location |
| `RUNS_DIR` | `./runs` | pipeline artifacts |
| `REGDOCS_DIR` | `agents/regulatory_docs/storage/documents` | regulatory document library |
| `VECTORIZATION_CONFIG` | `config/vectorization.json` | vectorization profiles |
| `STORAGE_CONFIG` | `config/storage.json` | per-agent / shared storage locations |
| `HANDOFF_DIR` | `storage/projects` | shared hand-off of each project's current BPMN model |
| `SEMANTIC_CACHE` | `noop` | `noop` \| `qdrant` (qdrant deferred) |
| `COORDINATOR_PORT` / `TEXT_TO_BPMN_PORT` | `8000` / `8100` | service ports |
| `LLM_PROVIDER` | `auto` | `auto` (pick by available key) \| `anthropic` \| `openai` |
| `PETRI_NET_PORT` | `8200` | Petri net generator service port |
| `PETRI_NET_RUNS_DIR` | `agents/petri_net_generator/storage/runs` | Petri net artifacts (IR, PNML, response) |
| `PETRI_NET_MAX_INPUT_BYTES` / `PETRI_NET_MAX_INLINE_PNML_BYTES` | `2000000` / `1000000` | input size limit / largest PNML returned inline (larger → `pnml_ref` only) |
| `PETRI_NET_MAX_IR_NODES` / `PETRI_NET_MAX_IR_DEPTH` | `500` / `25` | process-algebra IR size limits |
| `PETRI_NET_MAX_STATES` / `PETRI_NET_SOUNDNESS_TIMEOUT_S` | `20000` / `5` | soundness-check budget (exceeded → `not_checked`) |
| `ANTHROPIC_MODEL` / `OPENAI_MODEL` | `claude-sonnet-4-6` / `gpt-4o` | override the model per provider |

---

### 7. Testing

```bash
pytest -q      # 197 tests, offline by default (scripted LLM + heuristic classifier); no API keys required
pytest tests/regulatory_docs -q   # regulatory docs agent only
pytest tests/petri_net_generator -q -s   # Petri net generator; -s prints the text→BPMN→Petri net demo
```

The whole suite runs without keys or network: real LLM calls are exercised by monkeypatching the
`api/` layer, and the text→eFLINT labelling tests use the scripted provider. Tests that need an
optional extra self-skip when it is absent (e.g. the eFLINT ontology tests without `.[eflint]`, or
the OpenAI happy-path without `.[llm]`). The regulatory-docs tests use the `hash` embedder and an
in-memory vector store; `tests/regulatory_docs/test_cloud_qdrant.py` additionally writes to a
throwaway collection on the **live cloud Qdrant** (and deletes it) when `QDRANT_URL`/`QDRANT_API_KEY`
are set — otherwise it skips.

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
- ✅ **Offline Verification:** Comprehensive test suite (197 tests) runs offline using scripted LLM mocks.
- ✅ **DMN Logic:** Deterministic generation of FEEL rules from textual conditions.
- ✅ **Centralized, Pluggable LLM Access:** One `api/` package for all provider SDK calls; thin provider adapters; config-driven `auto`/`anthropic`/`openai` selection with an injectable per-agent LLM interface.
- ✅ **Text→eFLINT Agent (foundation):** Vendored FLINT ontology + SHACL validation, and provider-agnostic LLM labelling of legal text into FLINT act frames (semantic-role schema, EN/NL few-shot examples, roles→frame mapping).
- ✅ **Regulatory Docs Agent:** Upload/find regulatory documents (Markdown / txt / PDF, content-hash dedup) and vectorize a selected one into cloud or in-process Qdrant via config-driven profiles (OpenAI or offline hash embeddings), with provenance-rich, idempotent upserts; demo UI included and verified against the live cloud cluster.
- ✅ **Petri Net Generator Agent:** BPMN 2.0 XML / text→BPMN graph IR / text (via a process-algebra IR) → ordinary workflow Petri net → PNML (+DOT), with structural/workflow-net validation and a bounded soundness check. Tests assert behaviour (enumerated traces), PNML round-trips through PM4Py, and an end-to-end text→BPMN→Petri net demo.

**Provisioned / Future:**
- **Semantic Cache:** `QdrantSemanticCache` (embed project descriptions; find similar/existing projects) — seam shipped, implementation deferred.
- **External Sub-Agents:** Verification/critic, simulation (SpiffWorkflow), knowledge-graph — behind the same contract. `verify_model` is already recognized and state-gated; it returns "agent not available" until one is wired.
- **Text→eFLINT — full frames:** current labelling fills `acts`; extracting `preconditions`/conditions, populating `facts`/`duties`, filling `explanation`, and wiring the labeller to the ontology validator are the next steps.
- **Regulatory Docs — retrieval & coordinator routing:** a `search` intent to query a collection (feeding the semantic cache and verification agents), coordinator intent recognition for `upload_document`/`find_documents`/`vectorize_document`, and end-to-end checks of large-document batching with OpenAI embeddings.
- **Petri Net Generator — next steps:** a multi-turn clarification loop (today the user re-sends the description with answers); PNML compatibility tests against a target consumer (e.g. WoPeD; layout/graphics are not emitted yet); verified inclusive-gateway semantics; sub-processes and intermediate events; real-LLM evaluation of the text→IR step.
- **Executable Workflow Generation:** Direct-execution formats (e.g., SpiffWorkflow).
- **Auth & Storage:** Real authentication for `user_id`; optional DB-backed `ProjectStore`.

See `todo/<agent>/` (e.g. `todo/coordinator/IMPLEMENTATION_PLAN.md`, `todo/coordinator/PROGRESS.md`, `todo/regulatory_docs/PROGRESS.md`) for the full design and changelogs.
