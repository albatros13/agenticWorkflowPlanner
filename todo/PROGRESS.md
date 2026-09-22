# PROGRESS — Guideline-to-BPMN Agent (Phases 0–5)

Implementation status and code guide for the staged pipeline that turns
natural-language process descriptions (esp. clinical guidelines) into auditable
BPMN 2.0 + DMN. Full plan: [`todo/IMPLEMENTATION_PLAN.md`](todo/IMPLEMENTATION_PLAN.md).

**Core principle:** the LLM is a semantic front-end only. There is **no direct
text→BPMN path** — text becomes source-anchored *evidence*, evidence becomes a
structured *IR*, and BPMN/DMN are **deterministically compiled** from the IR and
validated against the official OMG schemas.

```
document → [1] evidence (Pass A) → [2] IR (Pass B) → [3] terminology
        → [5] BPMN + DMN compile → schema/graph validation
```

## How to run

```bash
pip install -e .[dev,bpmn]      # pydantic, lxml, xmlschema, pytest, httpx
python -m pytest -q             # 48 passed, fully offline (mocked LLM, no API keys)
```

Providers/stores (anthropic, openai, qdrant) are optional and **lazy-imported**, so
the package and its tests run without them. Set `LLM_PROVIDER=anthropic|openai` to
use a real model; secrets are read from `.env` and never logged.

## Demo UI (text → BPMN)

A FastAPI app + bpmn-js frontend demonstrates the conversion interactively.

```bash
pip install -e .[bpmn] anthropic     # a real LLM is required for extraction
uvicorn app.main:app --reload        # then open http://127.0.0.1:8000
```

Paste a process description (or upload a `.txt`/`.md`), press **Generate**, and the
page shows the extracted resources (actors, evidence, elements, terminology
mappings, DMN tables) and the rendered **BPMN diagram**, with live validation
badges (BPMN valid / connected / DMN valid) and a `.bpmn` download.

- **Backend** `app/main.py` — `POST /api/generate` runs ingest→evidence→IR→
  terminology→BPMN/DMN on the submitted text and returns `bpmn_xml`, `dmn_xml`,
  `resources`, and `validation`. Picks the provider from `LLM_PROVIDER` or whichever
  API key is set; pipeline/LLM failures return HTTP 422 with the reason.
- **Frontend** `app/static/index.html` — self-contained; renders BPMN with bpmn-js
  (CDN). File upload is read client-side (no `python-multipart` needed).
- The interpreter deterministically infers a missing `gateway_kind` (parallel→AND,
  else XOR) so imperfect model output still compiles instead of failing the demo.

---

## Phase 0 — Foundation (`src/config.py`, `src/llm/`, `src/pipeline/`)

- **`config.py`** — `Settings` from env, a dependency-free `.env` loader, prompt/
  pipeline version stamps, and `redact()` so secrets never reach logs/prompts.
- **`llm/`** — provider-agnostic interface. `base.LLMProvider.generate_json()`
  always returns a **schema-validated dict** (structured output, never free prose).
  - `mock.ScriptedLLMProvider` — routes canned responses by `task`; powers offline tests.
  - `anthropic_provider` / `openai_provider` — force structured output via a single
    required tool call; imported lazily. `factory.get_provider()` selects by settings.
- **`pipeline/artifacts.py`** — `ArtifactStore` persists every stage as an
  immutable version under `runs/<id>/` (never overwrites → audit trail). `require()`
  raises `MissingArtifactError` instead of silently continuing on missing input.
- **`content_provider.py`** — small file/image/HTML helpers (also satisfies the
  pre-existing import in `api/anthropic_client.py`).

## Phase 1 — Ingestion + evidence (`src/ingestion/`, `src/retrieval/`, `src/evidence/`)

- **`ingestion/`** — loaders behind a `DocumentLoader` abstraction produce a
  `Document` of typed `Block`s (heading/paragraph/list/table/quote) each carrying a
  `Location` (section path + line range, or page for PDF).
  - `markdown_loader` (dependency-free, exact line locations), `pdf_loader` (lazy `pypdf`).
  - `chunker.SemanticChunker` groups blocks **by section** (not raw token count),
    keeping block ids + line ranges so evidence stays traceable.
- **`retrieval/`** — `EvidenceIndex` returns the *original* chunk text + location.
  `in_memory.InMemoryEvidenceIndex` is a dependency-free TF-IDF index (default, runs
  offline); `qdrant_index` is a lazy production alternative behind the same interface.
- **`evidence/extractor.py`** (Pass A) — LLM selects atomic statements; the extractor
  **deterministically copies each item's location from its cited chunk** and
  **rejects any item citing an unknown chunk** — this blocks answers from model memory.

## Phase 2 — Intermediate Representation (`src/ir/`)

- **`enums.py`** — element/relation/gateway vocabularies and the uncertainty model
  `EXPLICIT | DERIVED | INFERRED | AMBIGUOUS | MISSING`.
- **`models.py`** — Pydantic `Process` / `Element` / `Relation` with `Threshold` and
  `TemporalConstraint`. Validators enforce the guideline rules:
  - invalid element types rejected (enum-typed);
  - a meaningful element with no evidence is rejected unless it is `DERIVED` and
    records `derived_from`;
  - gateways/decisions must declare explicit AND/OR/XOR semantics;
  - relation endpoints must reference existing elements; ids unique.
  - JSON round-trip preserves semantics exactly.

## Phase 3 — Semantic extraction / Pass B (`src/prompts/`, `src/logic/`)

- **`prompts/`** — versioned prompts kept out of business logic; each paired with a
  JSON Schema. Pass A and Pass B are **separate prompts** (one never both reads the
  doc and invents structure).
- **`logic/interpreter.py`** — turns evidence into the IR: the LLM proposes
  elements/relations, then the Pydantic IR enforces structure and the interpreter
  **rejects references to unknown evidence ids**, so every element stays provenanced.

## Phase 4 — Terminology grounding (`src/terminology/`, `config/terminology.json`)

- **`resolver.TerminologyResolver`** — annotates IR elements with controlled-
  vocabulary mappings (SNOMED/LOINC/RxNorm/ATC/…) loaded from a configurable JSON.
  It **never alters wording**: `annotate_process()` leaves the IR untouched and emits
  side annotations that keep the source `surface_form`; low-confidence/synonym
  matches are flagged `is_reviewable`; unmatched terms return `match_type=none`
  (a match is not required).

## Phase 5 — Deterministic BPMN + DMN compilation (`src/bpmn/`, `src/dmn/`, `src/validation/`)

- **`bpmn/model.py`** — lowers the IR into a flat control-flow graph and applies the
  mapping: Activity→task, Start/End→events, XOR→exclusiveGateway, OR→inclusiveGateway,
  AND→parallelGateway, actor→lane. A `Decision` **with thresholds** is expanded into a
  `businessRuleTask` + gateway (standard BPMN+DMN pattern), and branch conditions are
  turned into FEEL unary tests.
- **`bpmn/layout.py`** — deterministic diagram-interchange layout: longest-path ranks
  → columns, actors → lane rows, pool/lane bounds, 2-point edge waypoints (renders
  cleanly in bpmn-js).
- **`bpmn/compiler.py`** — serializes valid **BPMN 2.0** with `lxml`: collaboration/
  pool + `laneSet`, flow nodes, sequence flows with `conditionExpression`, the DMN
  link on the business-rule task via `extensionElements` (foreign-namespace, still
  schema-valid), and the full `BPMNDI` diagram. Stable ids ⇒ byte-identical output.
- **`dmn/compiler.py`** — generates **DMN 1.3** decision tables (one per thresholded
  decision): input per measure, FEEL tests per rule (`>= 25`, `< 25`, `[a..b]`),
  quoted string outputs naming each branch. `hitPolicy=FIRST`.
- **`validation/schema.py`** — deterministic validators against the **bundled OMG
  XSDs** in `schemas/` (`validate_bpmn`, `validate_dmn`), plus `roundtrip()` parse and
  `check_connectivity()` (start/end present, no orphans, reachable-from-start /
  can-reach-end).
- Wired into the pipeline as stage 5: `Pipeline.compile_bpmn()` / `run_through_bpmn()`,
  persisting `bpmn` and `dmn` artifacts.

**Scope note:** Phase 5 covers the control-flow subset. `DataObject`/`Message`
elements and data/message relations are captured in the IR but not yet rendered as
BPMN artifacts (planned with later phases).

---

## Test map (`tests/`)

| File | Covers |
| --- | --- |
| `test_ingestion.py` | markdown structure, tables kept whole, chunk locations |
| `test_retrieval.py` | the 6 guideline fixture cases each retrieve the right passage |
| `test_evidence_extraction.py` | evidence inherits chunk location; unanchored evidence rejected |
| `test_ir_models.py` | invalid type / missing-evidence / malformed-gateway / dangling-relation rejected; round-trip |
| `test_terminology.py` | annotation never alters names/thresholds; unmatched = none; low-confidence reviewable |
| `test_bpmn_compiler.py` | every IR fixture → schema-valid, connected BPMN; deterministic; gateway/lane mapping |
| `test_dmn_compiler.py` | valid DMN; FEEL rules from thresholds; BR-task→decision link |
| `test_validation.py` | broken models (malformed XML, illegal element, missing end, orphan, unreachable) caught |
| `test_smoke_pipeline.py` | end-to-end (ingest→evidence→IR→terminology→BPMN/DMN) with mocked LLM; explicit failure on missing artifact |
| `test_api.py` | `POST /api/generate` returns BPMN + resources (mocked LLM); gateway_kind normalization; empty-text rejected |

Fixtures: `fixtures/cases/*` (6 guideline categories), `fixtures/smoke_bph.md`,
`tests/_ir_fixtures.py` (hand-built IR for compiler tests). Schemas: `schemas/bpmn`,
`schemas/dmn` (official OMG XSDs).
