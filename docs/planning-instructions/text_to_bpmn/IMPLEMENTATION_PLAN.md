# Implementation Plan — Guideline-to-BPMN Agent

Derived from `todo/guideline_to_bpmn_agent_implementation.md`, adapted to the existing
`agenticWorkflowPlanner` repository (FastAPI + Pydantic v2, Anthropic/OpenAI clients,
Qdrant/Neo4j credentials already in `.env`).

## Progress

- ✅ **Phase 0 — Foundation**: `src/` package; `config.py` (versions, secret redaction, `.env`
  loader); swappable `LLMProvider` (`llm/` with scripted-mock + lazy Anthropic/OpenAI adapters +
  factory); versioned `ArtifactStore` that `require()`s upstream artifacts; `content_provider.py`
  (unblocks the existing `api/anthropic_client.py` import); `pyproject.toml` with optional-dep
  groups. Mocked-LLM smoke test passes end-to-end.
- ✅ **Phase 1 — Ingestion + evidence**: structure-preserving `MarkdownLoader` (+ line locations),
  lazy `PdfLoader`, section-aware `SemanticChunker`; retrieval interface with offline
  `InMemoryEvidenceIndex` (TF-IDF) + lazy `QdrantEvidenceIndex`; `EvidenceExtractor` (Pass A) that
  anchors every item to a real chunk and rejects unanchored evidence. Six fixture cases + retrieval
  tests.
- ✅ **Phase 2 — IR**: `ir/enums.py` (element/relation/gateway types + `EXPLICIT/DERIVED/INFERRED/
  AMBIGUOUS/MISSING`), `ir/models.py` (`Element`/`Relation`/`Process`, `Threshold`,
  `TemporalConstraint`) with validators: invalid types, missing-evidence, malformed-gateway, and
  dangling-relation rejection; JSON round-trip preserves semantics.
- ✅ **Phase 3 — Semantic extraction (Pass B)**: versioned prompts (`prompts/`) separate from logic;
  `ProcessLogicInterpreter` builds the validated IR from evidence and rejects unknown evidence refs.
- ✅ **Phase 4 — Terminology grounding**: configurable `config/terminology.json`,
  `TerminologyResolver` that annotates without altering wording, keeps source forms, and flags
  low-confidence matches reviewable.
- ✅ **Phase 5 — Deterministic BPMN + DMN compilation**: `src/bpmn/` lowers the IR to a control-flow
  graph and serializes BPMN 2.0 with `lxml` (stable ids, explicit gateway semantics, actor lanes +
  pool, full DI auto-layout). Thresholded `Decision`s expand into a `businessRuleTask` linked (via
  `extensionElements`) to a generated DMN 1.3 decision table (`src/dmn/`) with FEEL unary tests.
  `src/validation/schema.py` validates against the **bundled OMG BPMN 2.0 / DMN 1.3 XSDs**
  (`schemas/`), plus round-trip parse and a graph-connectivity check. Compilation wired into the
  pipeline as stage 5 (`compile_bpmn`). Scope note: control-flow subset only (DataObject/Message
  artifacts deferred to later phases).

Tests: `pytest -q` → **46 passed** (offline, no API keys). Run: `pip install -e .[dev,bpmn]` then
`pytest`. Next up: **Phase 6 — structural + coverage validation** (Steps 7–8).


## Guiding principle (non-negotiable)

The system behaves like a **verified process compiler with an LLM semantic front-end**, not a
chatbot that draws BPMN. At every stage prefer: *evidence over model memory, structure over free
text, deterministic compilation over generation, validation over trust, provenance over
after-the-fact explanation, human clarification over unsupported inference.*

There is **no direct `text → BPMN` path**. The pipeline is:

```
Source doc → evidence extraction → logic extraction → IR → BPMN (+DMN) compile
           → syntactic+semantic validation → coverage → critic → repair loop
           → scenario/execution validation → human review
```

---

## Agent architecture (how "delegation" maps onto the pipeline)

The product is an **orchestrator agent** that talks to the user in natural language and delegates
to specialized sub-agents. Delegation is deterministic (code-orchestrated), not free-form:

| Agent | Responsibility | Backed by pipeline stage |
| --- | --- | --- |
| **Orchestrator** | Converse with user, run the staged pipeline, manage review state, ask clarifying questions | Steps 10, 16, 19 |
| **Evidence agent** | Extract cited evidence chunks from the document | Steps 2, 4 (Pass A) |
| **Process-logic agent** | Interpret evidence into the structured IR (AND/OR/XOR, thresholds, temporal, exceptions) | Step 4 (Pass B) |
| **Terminology agent** | Ground clinical terms to controlled vocabularies (SNOMED/LOINC/RxNorm/ATC/ICD) | Step 5 |
| **BPMN/DMN agent** | *Not* an XML writer — it validates the IR is compilable and explains structure; the deterministic compiler does the writing | Step 6 |
| **Critic agent** | Independent adversarial review against source evidence | Step 9 |

LLM calls always use **structured output / tool calling** (extend the existing
`run_anthropic_tool_loop` in `api/anthropic_client.py`), never free prose that gets parsed.

---

## Technology decisions

- **Language/runtime**: Python 3.13 (repo `.venv`), reuse installed FastAPI 0.141 + Pydantic 2.13.
- **LLM access**: reuse `api/anthropic_client.py` / `api/openai_client.py` behind a new
  `LLMProvider` interface so provider is swappable and a **local/private model** can be plugged in
  for healthcare data (Step 18). Add `anthropic`, `openai` to deps (verify install).
- **Vector retrieval**: Qdrant (creds already in `.env`) via `api/qdrant_remote_client.py`.
- **BPMN generation & validation**: build XML deterministically with **`lxml`** against the
  **official BPMN 2.0 XSD**; add diagram interchange (DI) auto-layout. Do not let the LLM emit XML.
- **DMN**: generate DMN 1.3 decision tables deterministically (lxml + DMN XSD) for every decision
  with thresholds (e.g. PSA>10, IPSS ≥25%, prostate-size bands, TNM staging).
- **Execution/simulation**: **`SpiffWorkflow`** (executes BPMN + DMN in Python) for Step 12
  scenario validation.
- **Provenance/audit store**: start with versioned JSON artifacts on disk per stage; optionally
  Neo4j (creds present) for the bidirectional traceability graph (Step 11).
- **Frontend** (per `TODO.md`): React + **bpmn-js** (view/edit/save) + **dmn-js**; clicking a BPMN
  element reveals its provenance and source evidence.

---

## Target module layout

Create a `src/` package (the existing `anthropic_client.py` already imports `src.content_provider`,
so `src/` is expected but missing — create it):

```
src/
  ingestion/      # PDF/markdown loaders, structure-preserving semantic chunking
  retrieval/      # Qdrant index + evidence retrieval (returns text + location)
  evidence/       # evidence models + extraction (Pass A)
  logic/          # process-logic interpretation (Pass B)
  ir/             # Pydantic IR: Process/Element/Relation + uncertainty enum
  terminology/    # ontology resolver + mappings
  bpmn/           # deterministic IR→BPMN compiler + DI layout
  dmn/            # deterministic decision-table compiler
  validation/     # syntax, graph-structure, semantic validators
  coverage/       # source-to-process coverage metrics
  critique/       # independent critic agent
  repair/         # bounded generate→validate→critique→repair loop
  simulation/     # SpiffWorkflow scenario execution
  provenance/     # bidirectional trace store
  evaluation/     # structural/semantic/provenance/execution metrics
  llm/            # LLMProvider interface + Anthropic/OpenAI/local adapters
  orchestrator/   # agent that drives the pipeline + review state machine
  content_provider.py   # satisfy existing import; file/image/html helpers
api/              # existing LLM/vector clients (kept)
app/              # FastAPI routes (Step 19)
frontend/         # React + bpmn-js/dmn-js
tests/
fixtures/         # the 6+ guideline test cases + gold benchmark
```

Every stage is independently executable and testable; every stage persists its artifact and the
pipeline **fails explicitly** if a required upstream artifact is missing.

---

## Phased delivery

### Phase 0 — Foundation (Steps 1, 18 partial)
- Create `src/` skeleton + `content_provider.py` (unblocks existing import).
- Pin deps in `pyproject.toml`/`requirements.txt`: `anthropic openai qdrant-client lxml
  spiffworkflow xmlschema pytest neo4j`.
- `LLMProvider` interface wrapping existing clients; provider selection via config, secrets stay in
  `.env` (never in prompts/logs).
- **Smoke test**: minimal input passes through every stage with a *mocked* LLM.

### Phase 1 — Ingestion + evidence (Steps 2, 4-Pass A)
- Markdown + PDF loaders (the example ships as both `.md` and `.pdf`); preserve headings, lists,
  tables (the prostate-size and TNM tables matter).
- Semantic chunking; Qdrant index; retrieval returns exact text + section/page location.
- Evidence extraction with structured output; every evidence item has `id/text/source/section/
  page/confidence`.
- **Fixtures** (from the guideline's required cases): sequential, XOR decision, AND/concurrent,
  temporal constraint, exception, ambiguous language — the prostate case supplies all six.

### Phase 2 — IR (Step 3)
- Pydantic models: `Process`, `Element` (Start/End/Activity/Decision/Gateway/Event/Subprocess/
  DataObject/Message), `Relation` (sequence/parallel/conditional/message/data/dependency).
- Explicit semantic fields: AND/OR/XOR, eligibility, thresholds, temporal, loops, escalation,
  exceptions, contraindications, missing info, alternatives, termination.
- **Uncertainty enum**: `EXPLICIT | DERIVED | INFERRED | AMBIGUOUS | MISSING`.
- Validators reject: invalid element types, missing evidence refs (unless `DERIVED` with source
  refs), malformed gateways. Round-trip JSON must preserve semantics. Unit-test all of this.

### Phase 3 — Semantic extraction (Step 4-Pass B)
- Two-pass LLM extraction (facts, then process logic) via tool calling.
- Prompt rules: extract only what's stated, don't invent, separate explicit vs derived, preserve
  conditions/thresholds/temporals verbatim, record ambiguity, attach evidence refs to everything.
- Prompts live in versioned config outside business logic.
- Metrics on fixtures: activity/decision recall, condition & actor preservation, ordering,
  evidence coverage. Reject unreferenced non-`DERIVED` elements.

### Phase 4 — Terminology grounding (Step 5)
- Resolver with configurable ontology mappings; represent `surface_form / normalized_label /
  ontology / concept_id / match_type / confidence`.
- Never replace source wording; keep both; low-confidence stays reviewable.
- Tests prove normalization never alters drug identity, dosage, threshold, anatomical site,
  disease state, or test interpretation.

### Phase 5 — Deterministic BPMN + DMN compilation (Step 6)
- `IR → BPMN 2.0` compiler: Activity→task, Decision→gateway/conditional, Start/End→events,
  Actor→pool/lane, Parallel→parallel gateway, XOR→exclusive, OR→inclusive, Message→message flow,
  Data→data object. Stable IDs, valid sequence flows, no orphans.
- `Decision → DMN` decision tables for threshold logic; BPMN business-rule task references DMN.
- Auto-layout for DI so bpmn-js renders cleanly.
- Tests: XML + BPMN/DMN schema validation, parser round-trip, graph connectivity, on all fixtures.

### Phase 6 — Validation (Steps 7, 8)
- **Syntax**: valid XML, BPMN 2.0 conformance, valid element types/refs.
- **Graph**: unreachable/dead-end/orphan nodes, missing start/end, bad gateways, invalid flow
  endpoints, impossible loops, inconsistent split/join.
- **Semantic**: activity has actor where required, decisions have outgoing conditions, exclusive
  branches interpretable, parallel branches join, meaningful elements have evidence.
- **Coverage checker**: evidence/activity/decision/condition/actor/temporal/exception coverage +
  `unsupported_elements`, `unmapped_evidence_items`.
- Tests use deliberately broken models and deliberately omitted/invented/mis-ordered elements;
  each validator must catch its defect.

### Phase 7 — Critic + bounded repair (Steps 9, 10)
- Independent critic with access to **original evidence** (not just the diagram); returns structured
  findings `{severity, element_id, problem, evidence_refs, recommendation}`.
- Loop: generate → validate → critique → repair → re-validate, with a **max iteration cap**.
- Unresolved ambiguity → return a **clarification request** to the user, never a guess.
- Tests: valid models untouched, seeded errors repaired, ambiguity terminates safely, loop bounded.

### Phase 8 — Provenance + scenario execution (Steps 11, 12)
- Bidirectional trace: guideline sentence ↔ evidence ↔ rule ↔ IR element ↔ BPMN element (Neo4j
  optional). Answer "why is this task/XOR/condition here?" and "what source is unrepresented?".
- SpiffWorkflow simulation; synthetic cases: normal path, each branch, boundary values, missing
  data, contraindications, exceptions, escalation, treatment failure, repeated actions.
- Distinguish **structurally valid vs semantically plausible vs clinically validated**.

### Phase 9 — Evaluation, benchmark, regression (Steps 13, 14, 15)
- Metrics reported **independently** (no single collapsed score): structural (validity,
  connectivity, GED, node/edge P/R), semantic (activity/decision P/R, condition/ordering/actor
  accuracy, exception & temporal coverage), provenance, execution agreement.
- Gold benchmark with 10 categories (sequential → complex clinical guideline); prostate case is the
  complex clinical anchor, ideally clinician-annotated.
- Single regression command chaining all test tiers; results keyed by model/version/prompt/pipeline/
  benchmark/timestamp; CI fails on metric regression beyond thresholds.

### Phase 10 — API, UI, review, audit, security (Steps 16, 17, 18, 19)
- FastAPI routes: `POST /processes`, `/extract`, `/generate`, `/validate`, `/critique`, `/repair`,
  `/approve`; `GET /bpmn`, `/provenance`, `/validation`, `/uncertainties`.
- Review state machine: `DRAFT → MACHINE_VALIDATED → NEEDS_CLARIFICATION → HUMAN_REVIEW → APPROVED`;
  never label "clinically correct" on LLM-critic approval alone.
- React UI: BPMN (bpmn-js) + DMN (dmn-js) beside source evidence; click element → provenance.
- Audit log persists doc hash, evidence, retrieval config, model/prompt versions, all IR/BPMN/
  validation/critic/repair versions, human decisions — **never overwrite**.
- Security: minimize data to external LLMs, support local models, redact PII, keep secrets out of
  prompts/logs; test that sensitive values never hit ordinary logs.

---

## Acceptance criteria (Step 20)

Track against the guideline's final checklist — no direct LLM→BPMN, IR between language and BPMN,
provenance on every meaningful element, uncertainty states distinguished, deterministic compilation,
automatic syntax+graph validation, coverage measured, independent critic, bounded repair, ambiguity
→ clarification, scenario testing, gold benchmark, automated regression, mandatory human review,
full audit history, safe PHI handling.

## Suggested first milestone (thin vertical slice)

Phases 0-2 + a hand-built IR fixture compiled through Phase 5 to a schema-valid BPMN of the BPH
**medical-treatment** sub-path (finasteride vs alpha-blocker → 1-month/3-month follow-up → IPSS
≥25% XOR → continue/refer-back). This exercises XOR, thresholds, temporal constraints, and a DMN
table end-to-end on real content, with the mocked-LLM smoke test proving every stage runs.
```
