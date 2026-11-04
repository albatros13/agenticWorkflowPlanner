# text_to_bpmn — natural-language process text → BPMN 2.0 (+ DMN 1.3)

The `text_to_bpmn` agent compiles a process description (for example a clinical guideline) into a
schema-valid BPMN 2.0 model, plus DMN 1.3 decision tables when the text contains decision logic.
It treats the text as **evidence**: an LLM extracts source-anchored statements and interprets them
into a typed intermediate representation (IR). Code, not the LLM, then compiles the IR to XML. Every
task and decision in the model can be traced back to the line or page it came from.

- **Intent:** `engineer_process` (payload: `text`, optional `process_name`).
- **Registered:** in-process in `config/agents.json`; the coordinator routes process descriptions
  and "refine the model" requests to it.
- **Run instructions:** [`RUN.md`](../../RUN.md).

## The pipeline (`pipeline/`)

1. **Ingestion & evidence extraction** (`ingestion/`, `evidence/`): loads Markdown, plain text or
   PDF, splits it into section-aware chunks, and has the LLM extract atomic statements, each
   anchored to its source location.
2. **Intermediate representation** (`ir/`, `logic/`): the LLM interprets the evidence into a
   Pydantic model of the process: actors, elements, relations and thresholds. Elements that are not
   backed by extracted evidence are rejected.
3. **Terminology grounding** (`terminology/`, `config/terminology.json`): maps clinical terms to
   controlled vocabularies (SNOMED / LOINC) without changing the source wording.
4. **Deterministic compilation** (`bpmn/`, `dmn/`): renders the IR into BPMN 2.0 (with automatic
   layout) and DMN 1.3, with FEEL rules generated from the textual conditions. There is no direct
   LLM-to-XML path.
5. **Validation** (`validation/`, `schemas/`): validates against the bundled OMG BPMN 2.0 / DMN 1.3
   XSDs and checks graph connectivity.

Other parts of the package:

- `llm/`: the provider-agnostic LLM interface (`LLMProvider.generate_json`, structured output
  only). `AnthropicProvider` / `OpenAIProvider` are thin adapters over the repo-root `api/`
  package, and `ScriptedLLMProvider` is the offline mock used by the tests. `LLM_PROVIDER=auto`
  (default) picks a provider from whichever API key is set. The other agents reuse this layer.
- `retrieval/`: an in-memory index and a Qdrant index over the chunks.
- `content_provider.py`, `config.py`: content helpers and environment-backed settings.

## Agent, service and demo

| File | Role |
| --- | --- |
| `agent.py` | `TextToBpmnHandler`: runs the pipeline behind the agent contract. It is configured with an LLM `provider` or a `provider_factory` (default: `select_provider` from `shaping.py`). |
| `shaping.py` | Provider selection and the response `resources` (evidence, IR, terminology, model) shown in the UIs. |
| `service.py` | Standalone contract service, port 8120 (`TEXT_TO_BPMN_PORT`): `/agent/manifest`, `/agent/invoke_sync`, `/agent/invoke`, `/agent/jobs/{id}`, `/health`. |
| `demo/` | The original single-shot demo UI, a thin shim over the agent (`POST /api/generate`, `GET /api/health`). It renders the BPMN with bpmn-js on port 8080. |

The result `output` contains `run_id`, `provider`, `bpmn_xml`, `dmn_xml`, `resources`, and
`validation` (`bpmn_valid`, `bpmn_errors`, `dmn_valid`, `connectivity_ok`,
`connectivity_errors`).

## Storage

Each run is saved under `storage/runs/<run_id>/` (override with `RUNS_DIR`): `chunks`, `evidence`,
`ir`, `terminology`, `bpmn` and `dmn`. Re-saving an artifact creates a new version
(`<name>.v<n>.json`), and `<name>.json` always points at the latest one. After a model is
generated, the coordinator copies the BPMN XML to the shared hand-off area
(`storage/projects/<project_id>/model.bpmn`) for downstream agents such as the Petri net
generator.

## Running

```bash
uv sync --extra text_to_bpmn                                     # or: cd agents/text_to_bpmn && uv sync
uv run uvicorn agents.text_to_bpmn.demo.main:app --port 8080     # demo UI
uv run uvicorn agents.text_to_bpmn.service:app   --port 8120     # contract service
```

To use the standalone service from the coordinator, set
`"transport": "http", "base_url": "http://localhost:8120"` for `text_to_bpmn` in
`config/agents.json`.

## Tests

`tests/text_to_bpmn/` (60 tests) and `tests/test_coordinator*.py`, all offline with the scripted
provider. The sample guidelines in `tests/text_to_bpmn/fixtures/` cover a smoke case
(`smoke_bph.md`) and six constructs: sequence, XOR decision, parallel (AND), temporal
constraints, exception, and ambiguous language.
