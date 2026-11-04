# petri_net_generator — BPMN / IR / text → workflow Petri net → PNML

The `petri_net_generator` agent produces an ordinary place/transition **workflow net**: unweighted
arcs, one source place holding the initial token, and one sink place as the final marking. The
output is PNML for external Petri-net tools (for example the
[eFLINT verifier](../eflint_verifier/README.md)), optionally with a Graphviz DOT preview.

- **Intent:** `generate_petri_net`.
- **Registered:** in-process in `config/agents.json`; the coordinator routes Petri net requests to
  it and calls it before eFLINT verification when a project has only a BPMN model.
- **License note:** PM4Py is licensed **AGPL-3.0**. It is only pulled in by the `petri_net` extra
  and imported lazily, so the coordinator can register this agent without it.

## Input types (`input_type`)

| `input_type` | Required field | Path | LLM? |
| --- | --- | --- | --- |
| `bpmn_xml` | `bpmn_xml` (str) | BPMN 2.0 XML → PM4Py → net | no |
| `text_to_bpmn_ir` | `text_to_bpmn_ir` (object) | the `text_to_bpmn` agent's graph IR (`runs/<run_id>/ir.json`) → BPMN (with that agent's compiler) → net | no |
| `text` | `text` (str) | natural language → process-algebra IR → net | yes |
| `ir` | `ir` (object) | a process-algebra IR, e.g. after clarification or editing → net | no |

Optional fields: `model_name`, `output_formats` (`["pnml"]` by default; add `"dot"`),
`check_soundness` (default `true`).

- **`bpmn_xml`:** parsed with a hardened lxml parser (no DTDs, entity expansion or network access)
  and checked against an allow-list before conversion. Supported: start/end events, tasks (all task
  types), sequence flows, exclusive and parallel gateways, and lanes/pools of a single process.
  Inclusive gateways are converted with a warning (PM4Py heuristic, not verified). Anything else
  (sub-processes, boundary/intermediate events, event-based/complex gateways, message flows, loop
  markers, multiple processes) is **rejected with a structured error** instead of being silently
  dropped.
- **`text`:** one structured LLM call (`LLMProvider.generate_json`) produces a typed, versioned
  **process-algebra IR** (`activity` / `sequence` / `xor` / `parallel` / `loop`, plus
  `assumptions` and `ambiguities`). Blocking ambiguities return
  `status: "needs_clarification"` with questions instead of a net. It uses the same LLM provider
  settings as `text_to_bpmn`.
- **`ir`:** compiled **deterministically** (`compiler.py`), never by the LLM.

## Validation and output

Every net goes through the same checks (`net_validation.py`): unique IDs, no dangling or weighted
arcs, well-defined markings, a single source and sink, and every node on a source→sink path. An
optional bounded **soundness** check (option to complete, proper completion, no dead transitions,
boundedness) reports `passed` / `failed` / `not_checked`. It reports `passed` only when the full
state space was explored within the budget.

The response contains `status`, `process_name`, `net_summary`, `initial_marking`,
`final_marking`, `validation` (`structurally_valid`, `workflow_net`, `soundness`, `findings`),
`assumptions`, `ambiguities`, `warnings`, `errors`, `ir`, `pnml`, `pnml_ref` and `dot`. The PNML
has deterministic IDs and PM4Py's `finalmarkings` extension, and round-trips through PM4Py. PNML
larger than `PETRI_NET_MAX_INLINE_PNML_BYTES` is only returned by reference (`pnml_ref`).

## Code layout

| File | Role |
| --- | --- |
| `agent.py` | `PetriNetGeneratorHandler`: payload validation and the `AgentResult` wrapper. `needs_clarification` is a *successful* result. |
| `service.py` | FastAPI service, port 8200: typed `POST /generate` plus the agent contract. |
| `generator.py` | Orchestrates the four input paths, validation and export. |
| `schemas.py` | `GenerateRequest` / `GenerateResponse`. |
| `ir.py`, `text_to_ir.py` | Process-algebra IR (Pydantic) and the LLM text→IR step. |
| `compiler.py` | Deterministic IR → PM4Py Petri net. |
| `bpmn_converter.py` | Secure BPMN parsing, allow-list scan and PM4Py conversion. |
| `net_validation.py` | Structural / workflow-net checks, bounded soundness, trace enumeration. |
| `exporters.py` | PNML (stable IDs) and DOT. |
| `config.py` | Environment-backed limits. |

## Running

```bash
uv sync --extra petri_net_generator      # or: cd agents/petri_net_generator && uv sync
uv run uvicorn agents.petri_net_generator.service:app --port 8200
# or: uv run python -m agents.petri_net_generator.service   (port from PETRI_NET_PORT)
```

```bash
jq -n --rawfile xml process.bpmn '{input_type: "bpmn_xml", bpmn_xml: $xml, output_formats: ["pnml","dot"]}' \
  | curl -s -X POST localhost:8200/generate -H 'Content-Type: application/json' -d @- \
  | jq -r '.pnml' > process.pnml
```

`POST /generate` returns HTTP 200 for `succeeded` and `needs_clarification` (resubmit with
`input_type: "ir"` and the returned or edited `ir`), and HTTP 422 for `failed` (see `errors`). The
service also serves the agent contract (`/agent/manifest`, `/agent/invoke_sync`, `/agent/invoke`,
`/agent/jobs/{id}`, `/health`). To use it from the coordinator, set
`"transport": "http", "base_url": "http://localhost:8200"` for `petri_net_generator` in
`config/agents.json`. More examples are in [`RUN.md`](../../RUN.md).

## Configuration

| Var | Default | Purpose |
| --- | --- | --- |
| `PETRI_NET_PORT` | `8200` | Service port. |
| `PETRI_NET_RUNS_DIR` | `agents/petri_net_generator/storage/runs` | Run artifacts. |
| `PETRI_NET_MAX_INPUT_BYTES` / `PETRI_NET_MAX_INLINE_PNML_BYTES` | `2000000` / `1000000` | Input size limit / largest PNML returned inline. |
| `PETRI_NET_MAX_IR_NODES` / `PETRI_NET_MAX_IR_DEPTH` | `500` / `25` | Process-algebra IR size limits. |
| `PETRI_NET_MAX_STATES` / `PETRI_NET_SOUNDNESS_TIMEOUT_S` | `20000` / `5` | Soundness-check budget (exceeded → `not_checked`). |

## Storage

Each run is saved under `storage/runs/<pnml_ref>/`: `net.pnml`, `response.json` and, for IR-based
paths, `ir.json`.

## Tests

`tests/petri_net_generator/` (43 tests) and `tests/test_coordinator_petri_net.py`. They assert
behaviour through enumerated traces, check that PNML round-trips through PM4Py, and include an
end-to-end text → BPMN → Petri net demo (`pytest tests/petri_net_generator -s` prints it). They
skip when PM4Py is not installed.

## Next steps

- A multi-turn clarification loop (today the user re-sends the description with answers).
- PNML compatibility tests against a target consumer such as WoPeD (layout/graphics are not
  emitted yet).
- Verified inclusive-gateway semantics; sub-processes and intermediate events.
- Real-LLM evaluation of the text → IR step.
