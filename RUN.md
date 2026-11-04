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
uv run uvicorn agents.regulatory_docs.demo.main:app  --port 8110   # regulatory docs (upload / find / vectorize to Qdrant)
uv run uvicorn agents.petri_net_generator.service:app --port 8200   # text / BPMN -> Petri net (PNML)
uv run uvicorn agents.petri_net_generator.demo.main:app --port 8210  # Petri net viewer (token game)
uv run uvicorn agents.eflint_verifier.service:app    --port 8300   # Petri net vs eFLINT verification (needs the external service, below)
```

You can also install a single agent's deps into the shared env with its extra, e.g.
`uv sync --extra regulatory_docs` (extras: `text_to_bpmn`, `text_to_eflint`, `coordinator`,
`regulatory_docs`, `petri_net_generator`, `eflint_verifier`; capability bundles: `llm`, `retrieval`, `ingestion`, `bpmn`,
`eflint`, `petri_net`).

## Option B — one agent on its own (isolated env per agent)

Each agent folder is its own project that installs only that agent's dependencies (the monorepo
is pulled in editable, so shared source stays importable). From the agent's directory:

```bash
cd agents/text_to_bpmn     && uv sync && uv run uvicorn agents.text_to_bpmn.demo.main:app    --port 8080
cd agents/text_to_eflint   && uv sync && uv run uvicorn agents.text_to_eflint.demo.main:app  --port 8090
cd agents/text_to_eflint   && uv sync && uv run uvicorn agents.text_to_eflint.service:app    --port 8091
cd agents/coordinator      && uv sync && uv run uvicorn agents.coordinator.service:app       --port 8070
cd agents/regulatory_docs  && uv sync && uv run uvicorn agents.regulatory_docs.demo.main:app --port 8110
cd agents/petri_net_generator && uv sync && uv run uvicorn agents.petri_net_generator.service:app --port 8200
cd agents/petri_net_generator && uv sync && uv run uvicorn agents.petri_net_generator.demo.main:app --port 8210
cd agents/eflint_verifier  && uv sync && uv run uvicorn agents.eflint_verifier.service:app   --port 8300
```

`uv sync` builds a local `.venv/` (git-ignored) in that agent's folder. The per-agent dependency
set is defined once in the root `pyproject.toml` (the `text_to_bpmn` / `text_to_eflint` /
`coordinator` / `regulatory_docs` / `petri_net_generator` / `eflint_verifier` extras); each `agents/<agent>/pyproject.toml` just selects it.

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

**Viewer:** `uv run uvicorn agents.petri_net_generator.demo.main:app --port 8210`, then open
http://localhost:8210. You can browse the saved nets, convert text_to_bpmn runs, upload any `.pnml`,
and click transitions to play the token game (see the agent README).

**From a saved text_to_bpmn run** (no service needed), e.g. a model generated earlier through the
coordinator or the text_to_bpmn demo:

```bash
uv run python -m agents.petri_net_generator.from_bpmn_run                  # list runs
uv run python -m agents.petri_net_generator.from_bpmn_run <run_id> --out nets/ --dot
```

See [`agents/petri_net_generator/README.md`](agents/petri_net_generator/README.md#converting-a-text_to_bpmn-run).

## eFLINT verifier (Petri net vs eFLINT specification)

The `eflint_verifier` agent verifies a process (a Petri net, PNML or textual `.pn`) against an
eFLINT specification through qualification rules. It is a client of the **external** eFLINT
model-checking webservice (`eflint-model-checking`, Rust + Clingo), which must be running
separately. Details: [`agents/eflint_verifier/README.md`](agents/eflint_verifier/README.md).

**1. Start the external service** (default `http://127.0.0.1:8100`; options after `--` include
`--port`, `--bind`, `--workers`):

```bash
cd <path-to>/eflint-model-checking
cargo run -p eflint-webservice
```

If it runs elsewhere, set `EFLINT_VERIFIER_URL` (e.g. in `.env`).

**2a. Through the coordinator** (the agent runs in-process; no extra service needed). Open a
project, or start without one, and paste the inputs as fenced code blocks:

````text
Verify the process against this eFLINT specification, bound 10:
```eflint
Fact recorded Identified by String.
Event register Holds when True Creates recorded("case").
```
```qualification
Sequence
    begin, finish
Qualify as
    register.
```
```pn
place ready = 1
place active
place done
transition begin: ready -> active
transition finish: active -> done
```
````

Without a ```` ```pnml ```` / ```` ```pn ```` block, the project's Petri net is used, or one is
generated from its BPMN model. Without an ```` ```analysis ```` block, the verifier checks that no
normative violation occurs within the bound. The inputs are kept with the project, so
*"verify against the eFLINT spec again"* reuses them.

**2b. As a standalone service:**

```bash
uv sync --extra eflint_verifier        # or: cd agents/eflint_verifier && uv sync
uv run uvicorn agents.eflint_verifier.service:app --port 8300
# or: uv run python -m agents.eflint_verifier.service   (port from EFLINT_VERIFIER_PORT, default 8300)
```

```bash
jq -n --rawfile pn process.pn --rawfile norms norms.eflint --rawfile rules rules.qr \
  '{physical: {language: "petri-net", source: $pn},
    normative: {language: "eflint", source: $norms},
    qualification: $rules, options: {max_states: 10}}' \
  | curl -s -X POST localhost:8300/verify -H 'Content-Type: application/json' -d @- \
  | jq '{verdict, operation, summary, renamed}'
```

Payload fields:

- `physical` (required) and `normative`: `{language: "petri-net" | "eflint", format?: "pn" | "pnml",
  source, dependencies?}`. The Petri net format is detected when omitted. `normative` and
  `qualification` go together.
- `qualification`: the rules mapping process transitions to eFLINT acts/events/facts.
- `analysis`: `Property` / `Compliance-checking` / `Plan` declarations. When omitted, a compliance
  check over all transitions is generated.
- `operation`: `auto` (default: chosen from the analysis), `model-check`, `compliance-check`,
  `plan`, `run`, `explore` (with `scenario`), `validate` or `compile` (with `target`).
- `options`: `max_states` (search bound, default 30), `all`, `verbose`, `timeout_seconds`.
- `normalize_pnml` (default `true`): convert PNML to the service's profile and naming rules.

The response has `verdict` (`passed` / `failed` / `inconclusive`), `summary`, `properties`
(outcome, bound and witness steps for each property), the `analysis` that was submitted,
`renamed` (Petri net names changed for the service) and the raw service result. `POST /verify`
returns HTTP 502 when the external service fails or cannot be reached. The service also exposes the
shared agent contract (`/agent/manifest`, `/agent/invoke_sync`, `/agent/invoke`, `/health`).

Each verification is saved under `agents/eflint_verifier/storage/runs/<run_id>/` (override with
`EFLINT_VERIFIER_RUNS_DIR`). Polling and limits are tunable with `EFLINT_VERIFIER_WAIT_TIMEOUT_S`
(default 300), `EFLINT_VERIFIER_MAX_STATES` (default 30), `EFLINT_VERIFIER_REQUEST_TIMEOUT_S` and
`EFLINT_VERIFIER_POLL_INTERVAL_S`.

## Tests

```bash
uv sync --extra dev
uv run pytest -q
uv run pytest -q tests/petri_net_generator tests/test_coordinator_petri_net.py   # Petri net agent only
uv run pytest -q tests/text_to_eflint                                            # text -> eFLINT agent only
uv run pytest -q tests/eflint_verifier tests/test_coordinator_eflint.py           # eFLINT verifier (offline, fake service)
EFLINT_VERIFIER_LIVE=1 uv run pytest -q tests/eflint_verifier                     # + live tests against the real service on :8100
                                                    # (add EFLINT_VERIFIER_KEEP_RUNS=1 to keep run records in the agent's storage)
```

Run the tests with the project environment (`uv run pytest`, or `pytest` inside an activated
`.venv`). A system `pytest` (e.g. `/usr/bin/pytest`) does not have the project's dependencies.
