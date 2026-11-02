# Running the agents

Secrets (API keys, `QDRANT_URL`/`QDRANT_API_KEY`, `OPENAI_API_KEY`, …) are read from the
repo-root `.env` at runtime.

## Option A — whole stack (one env for all agents)

Install everything and run any agent from the repo root:

```bash
uv sync --extra all          # all agents' runtime deps  (add --extra dev for tests)
# or: pip install -e '.[all]'

uv run uvicorn agents.text_to_bpmn.demo.main:app     --port 8080   # text -> BPMN
uv run uvicorn agents.text_to_eflint.demo.main:app   --port 8090   # text -> eFLINT (labelling + generation UI)
uv run uvicorn agents.text_to_eflint.service:app     --port 8091   # text -> eFLINT agent service
uv run uvicorn agents.coordinator.service:app        --port 8070   # coordinator
uv run uvicorn agents.regulatory_docs.demo.main:app  --port 8100   # regulatory docs (upload / find / vectorize to Qdrant)
uv run uvicorn agents.petri_net_generator.service:app --port 8200   # text / BPMN -> Petri net (PNML)
```

You can also install a single agent's deps into the shared env with its extra, e.g.
`uv sync --extra regulatory_docs` (extras: `text_to_bpmn`, `text_to_eflint`, `coordinator`,
`regulatory_docs`, `petri_net_generator`; capability bundles: `llm`, `retrieval`, `ingestion`, `bpmn`,
`eflint`, `petri_net`).

## Option B — one agent on its own (isolated env per agent)

Each agent folder is its own project that installs only that agent's dependencies (the monorepo
is pulled in editable, so shared source stays importable). From the agent's directory:

```bash
cd agents/text_to_bpmn     && uv sync && uv run uvicorn agents.text_to_bpmn.demo.main:app    --port 8080
cd agents/text_to_eflint   && uv sync && uv run uvicorn agents.text_to_eflint.demo.main:app  --port 8090
cd agents/text_to_eflint   && uv sync && uv run uvicorn agents.text_to_eflint.service:app    --port 8091
cd agents/coordinator      && uv sync && uv run uvicorn agents.coordinator.service:app       --port 8070
cd agents/regulatory_docs  && uv sync && uv run uvicorn agents.regulatory_docs.demo.main:app --port 8100
cd agents/petri_net_generator && uv sync && uv run uvicorn agents.petri_net_generator.service:app --port 8200
```

`uv sync` builds a local `.venv/` (git-ignored) in that agent's folder. The per-agent dependency
set is defined once in the root `pyproject.toml` (the `text_to_bpmn` / `text_to_eflint` /
`coordinator` / `regulatory_docs` / `petri_net_generator` extras); each `agents/<agent>/pyproject.toml` just selects it.

## eFLINT generator (regulatory text -> executable eFLINT)

The `text_to_eflint` agent's `generate_eflint` intent turns regulatory text into an eFLINT
specification: facts, duties, acts, domain instances, initial state, and scenarios. An LLM drafts
the specification. It is then parsed, type-checked, its source quotes are checked against the
input text, and its scenarios are executed. Any errors go back to the LLM for up to
`max_repair_rounds` repairs.

The modelling approach follows the *data governance framework* by Ericmolenaar/Scattered-Directive
(MIT). It is vendored under `agents/text_to_eflint/generation/reference/`, where it serves as the
LLM's worked example and as golden tests. It uses the same LLM provider settings as `text_to_bpmn`
(`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` in `.env`, and `LLM_PROVIDER`).

```bash
uv sync --extra text_to_eflint
uv run uvicorn agents.text_to_eflint.demo.main:app --port 8090   # UI: paste text -> "Generate eFLINT"
uv run uvicorn agents.text_to_eflint.service:app   --port 8091   # API
```

```bash
curl -s -X POST localhost:8091/generate -H 'Content-Type: application/json' -d '{
  "text": "Article 7(3). The data subject shall have the right to withdraw his or her consent at any time.",
  "domain_context": "a smart-meter company processes household energy readings",
  "max_repair_rounds": 2
}' | jq '{status, rounds, summary, findings}'
```

Payload fields:

- `text`, or `document_id` (a `regulatory_docs` library document), optionally narrowed with
  `sections: ["Article 6", ...]`.
- `title` and `domain_context`. The context is used to choose instances and scenarios.
- `base_spec`: an existing `.eflint` specification to extend.
- `scenarios` (default `true`), `max_repair_rounds` (default `2`).
- `srl_hints` (default `false`): label the normative sentences with the FlintFiller SRL labeller
  first and pass the frames to the model as hints. This costs one LLM call per sentence.

The response has these fields:

- `status`: `succeeded`, or `needs_review` when errors remain after the repairs.
- `summary`, `findings` (error / warning / info) and `scenarios` (results for each step).
- `spec`: the structured specification.
- `files`: `Ontology.eflint`, `Norms.eflint`, `Init.eflint`, `Scenario-<n>.eflint`, and the
  self-contained `spec.eflint`.

Each run is saved under `agents/text_to_eflint/storage/runs/<run_id>/`. Set `EFLINT_RUNS_DIR` to
save runs somewhere else. `POST /validate` checks a specification and runs its scenarios without
an LLM. It takes either `{"spec": {...}}` or `{"eflint": "...", "scenarios": {"name": "..."}}`.

Scenarios are executed by a small built-in simulator for the eFLINT subset the generator emits.
The official eFLINT interpreter remains the authority. Long documents (more than 60k characters)
must be split by article. Pass each earlier result as `base_spec` to build up the specification.
Through the coordinator, the agent is registered in-process for the intents `generate_eflint`
and `validate_eflint` (see `config/agents.json`).

## Petri net generator (text or BPMN -> Petri net)

Generates an ordinary workflow Petri net (PNML, optionally Graphviz DOT) with structural and
soundness validation. Needs the `petri_net_generator` extra (pulls in PM4Py, licensed AGPL-3.0).

```bash
uv sync --extra petri_net_generator          # or use Option A / Option B above
uv run uvicorn agents.petri_net_generator.service:app --port 8200
# or: uv run python -m agents.petri_net_generator.service   (port from PETRI_NET_PORT, default 8200)
```

Call the typed endpoint `POST /generate`. `input_type` selects which input field is required:

| `input_type`      | required field     | path                                             | LLM? |
|-------------------|--------------------|--------------------------------------------------|------|
| `bpmn_xml`        | `bpmn_xml` (str)   | BPMN 2.0 XML -> PM4Py -> net                     | no   |
| `text`            | `text` (str)       | NL description -> process-algebra IR -> net      | yes  |
| `ir`              | `ir` (object)      | process-algebra IR (e.g. edited/clarified) -> net | no   |
| `text_to_bpmn_ir` | `text_to_bpmn_ir`  | the `text_to_bpmn` agent's graph IR -> BPMN -> net | no |

Optional fields: `model_name`, `output_formats` (`["pnml"]` default, add `"dot"`),
`check_soundness` (default `true`).

**From BPMN** (no LLM needed):

```bash
jq -n --rawfile xml process.bpmn '{input_type: "bpmn_xml", bpmn_xml: $xml, output_formats: ["pnml","dot"]}' \
  | curl -s -X POST localhost:8200/generate -H 'Content-Type: application/json' -d @- \
  | jq -r '.pnml' > process.pnml
```

**From text** (uses the same LLM provider as `text_to_bpmn`: `ANTHROPIC_API_KEY` or
`OPENAI_API_KEY` in `.env`; force one with `LLM_PROVIDER=anthropic|openai`):

```bash
curl -s -X POST localhost:8200/generate -H 'Content-Type: application/json' -d '{
  "input_type": "text",
  "text": "A customer submits an order. The clerk checks stock; if available the order is shipped, otherwise it is rejected."
}' | jq '{status, net_summary, validation, assumptions, ambiguities}'
```

Responses: HTTP 200 with `status` = `succeeded` or `needs_clarification` (see `ambiguities`;
resubmit with `input_type: "ir"` and the returned/edited `ir`), HTTP 422 with `status: "failed"`
(see `errors`). Each run is saved under `agents/petri_net_generator/storage/runs/<pnml_ref>/`
(`net.pnml`, `response.json`, `ir.json`); override with `PETRI_NET_RUNS_DIR`. Limits are tunable
via `PETRI_NET_MAX_INPUT_BYTES`, `PETRI_NET_MAX_STATES`, `PETRI_NET_SOUNDNESS_TIMEOUT_S`, etc.
(see `agents/petri_net_generator/config.py`).

The service also exposes the shared agent contract (`/agent/manifest`, `/agent/invoke_sync`,
`/agent/invoke`, `/health`). Through the coordinator the agent runs **in-process** by default
(intent `generate_petri_net`, see `config/agents.json`); to use the standalone service instead set
`"transport": "http", "base_url": "http://localhost:8200"` for `petri_net_generator`.

## Tests

```bash
uv sync --extra dev
uv run pytest -q
uv run pytest -q tests/petri_net_generator tests/test_coordinator_petri_net.py   # Petri net agent only
uv run pytest -q tests/text_to_eflint                                            # text -> eFLINT agent only
```
