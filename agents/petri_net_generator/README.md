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
| `from_bpmn_run.py` | Command line: convert a saved `text_to_bpmn` run (see below). |
| `demo/` | Viewer web app: `main.py` (FastAPI), `view.py` (PNML → view model, token game, DOT), `static/index.html`. |

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

## Viewer demo (web app)

`demo/` is a small web application that visualizes Petri nets and plays the token game:

```bash
uv run uvicorn agents.petri_net_generator.demo.main:app --port 8210     # open http://localhost:8210
```

- **Open a net:** pick one saved by the generator (`storage/runs/<pnml_ref>/net.pnml`), convert a
  saved `text_to_bpmn` run with one click, or upload/paste any `.pnml` file. `/?ref=<pnml_ref>`
  opens a saved net directly.
- **Token game:** enabled transitions are drawn in green. Click one in the diagram (or in the
  *Enabled transitions* list) to fire it. The page shows the fired sequence and the current marking,
  and says when the final marking or a deadlock is reached. Undo and Reset are available.
- **Display:** zoom and pan, left→right or top→bottom layout, and export as SVG or PNML. On open it
  also shows the structural findings and the soundness verdict.

Existing libraries do the standard work:
- **PM4Py** parses the PNML and provides the firing semantics.
- **Graphviz** draws the net in the usual Petri net notation, running in the browser as WebAssembly
  ([`@viz-js/viz`](https://github.com/mdaines/viz-js)). No system install is needed, but the page
  loads it and **svg-pan-zoom** from jsDelivr, so the browser needs internet access. `POST /api/svg`
  renders on the server instead when the Graphviz `dot` binary is installed.

PNML `<graphics><position>` coordinates (from editors such as WoPeD or YAPNE) are honoured: when
every node has one, the drawing keeps the editor's layout. When a file declares no final marking,
PM4Py infers one (the places without outgoing arcs), and the viewer labels it as inferred.

Uploaded documents are parsed with a hardened XML parser (no DTDs, entities or network access)
before they reach PM4Py. The API is stateless: each request carries the PNML (or a saved net's
`ref`) and the current marking.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/nets` | Saved nets, newest first (name, input type, size, soundness). |
| `GET /api/nets/{ref}/pnml` | Download a saved net. |
| `POST /api/view` | `{pnml` or `ref, marking?, rankdir?, analyze?}` → nodes, enabled transitions, status, DOT, findings, soundness. |
| `POST /api/fire` | `{pnml` or `ref, marking, transition}` → the view after firing. |
| `GET /api/bpmn_runs`, `POST /api/convert` | List / convert `text_to_bpmn` runs (same as `from_bpmn_run`). |
| `POST /api/svg` | Server-side Graphviz rendering of a DOT document (fallback). |

## Converting a text_to_bpmn run

`from_bpmn_run.py` converts the model of a saved `text_to_bpmn` run
(`agents/text_to_bpmn/storage/runs/<run_id>/`, or `RUNS_DIR`) without starting any service:

```bash
uv run python -m agents.petri_net_generator.from_bpmn_run                    # list runs that have a BPMN model
uv run python -m agents.petri_net_generator.from_bpmn_run 22b4425994a4       # convert one run (id or folder path)
uv run python -m agents.petri_net_generator.from_bpmn_run --all --out nets/ --dot   # every run; copy PNML + DOT to nets/
```

It reads the BPMN XML from the run's `bpmn.json` (or the graph IR from `ir.json` with
`--source ir`) and converts it through the agent handler, so each net is validated,
soundness-checked and saved under this agent's `storage/runs/<pnml_ref>/net.pnml`. `--out DIR`
also writes `<run_id>.pnml` (and `.dot`). Other options: `--no-soundness`, `--runs-dir`. It prints
one line per run. The exit status is 0 if every run converted, 1 if any failed (for example an
unsupported BPMN element), and 2 for an unknown run or a run without `bpmn.json`.

This is a developer tool: it reads another agent's private run folder. At runtime the coordinator
passes models through the shared hand-off area instead.

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

`tests/petri_net_generator/` (59 tests, including the `from_bpmn_run` command and the viewer demo) and `tests/test_coordinator_petri_net.py`. They assert
behaviour through enumerated traces, check that PNML round-trips through PM4Py, and include an
end-to-end text → BPMN → Petri net demo, which `-s` prints:

```bash
uv run pytest tests/petri_net_generator -s     # or `pytest ...` inside an activated .venv
```

Run them in the project environment. A system `pytest` (e.g. `/usr/bin/pytest`) lacks the
project's dependencies, and the suite then stops with a message saying so. In a project env
installed without the `petri_net_generator` extra, each module is reported as skipped with the
reason "PM4Py is not installed".

## Next steps

- A multi-turn clarification loop (today the user re-sends the description with answers).
- PNML compatibility tests against a target consumer such as WoPeD (layout/graphics are not
  emitted yet).
- Verified inclusive-gateway semantics; sub-processes and intermediate events.
- Real-LLM evaluation of the text → IR step.
