# eflint_verifier agent

Verifies a process (Petri net) against an eFLINT specification by calling the **external**
eFLINT model-checking webservice (`eflint-model-checking`, started with
`cargo run -p eflint-webservice`, default `http://127.0.0.1:8100`). The coordinator calls it when a
user asks to verify a process against an eFLINT spec.

## The agent

- The service has its own job API. `eflint_verifier` is an agent that calls it over HTTP. The agent submits the job, polls with backoff, fetches
  the result, then deletes the job. If a job runs longer than the wait timeout, it cancels it.
- It returns one verdict (`passed` / `failed` / `inconclusive`), the outcome of each property, and
  the first counterexample or plan as a list of steps. A found counterexample is a successful call
  with verdict `failed`. A real error (service unreachable, request rejected, compile error,
  timeout) is a failed call.
- If no analysis is supplied, it generates a compliance check over all Petri-net transitions
  ("no violation within the bound").
- Each run is saved under `agents/eflint_verifier/storage/runs/<id>/`.
- It also has a standalone service like the other agents
  (`uvicorn agents.eflint_verifier.service:app --port 8300`).
- Settings come from environment variables: `EFLINT_VERIFIER_URL` (default
  `http://127.0.0.1:8100`), plus `EFLINT_VERIFIER_WAIT_TIMEOUT_S` and `EFLINT_VERIFIER_MAX_STATES`.
- It's registered in `config/agents.json`, `config/storage.json` and
  `agents/coordinator/factory.py`, and has an `eflint_verifier` extra (httpx).

## Demo app

`uv run uvicorn agents.eflint_verifier.demo.main:app --port 8310`, then open http://localhost:8310.

- Supply the inputs by pasting, uploading a file, or picking from other agents' storage:
  - the Petri net (PNML or `.pn`): pick a net saved by `petri_net_generator`;
  - the eFLINT norms: pick the self-contained `spec.eflint` of a `text_to_eflint` run;
  - the qualification rules;
  - an optional analysis and scenario.
- Set the operation, bound, timeout and flags, then click **Verify**. *Load sample* fills in a small
  worked example.
- The result shows the verdict, the outcome of each property with its witness trace, the process
  transitions (useful when writing qualification rules), PNML renames, the analysis that was
  submitted, and the raw service result.
- **Stored verification runs** lists `storage/runs/`. Open a run to see its report, and restore its
  inputs into the form to re-run or edit it.
- A badge in the header shows whether the external service (`EFLINT_VERIFIER_URL`) is reachable.
  Browsing works without the service.

## PNML conversion

The service rejected the PNML from `petri_net_generator`. It only accepts a stricter PNML layout,
and our file includes a `finalmarkings` element it doesn't support. Task names like
"Submit request" also break its naming rules. The wrapper therefore converts the PNML before
sending it (`pnml.py`): it adjusts the format, drops the final marking, and renames nodes to slugs
like `submit-request`. It reports the renames, because qualification rules have to use the new
names. The converted file is accepted by the live service.

## Coordinator

- **Trigger:** the `verify_eflint` intent, for verify/check/comply wording plus
  eFLINT/norm/regulation, or an attached eFLINT block. It picks up a search bound from phrases like
  "bound 12" or "within 12 steps". "Verify the model" also goes to the verifier when the project
  already has an eFLINT spec.
- **How users supply inputs:** they paste them as fenced code blocks (```` ```eflint ````,
  ```` ```qualification ````, ```` ```analysis ````, ```` ```pnml ````/```` ```pn ````). These are
  split off before classification and saved with the project, so "verify again" needs no
  re-upload.
- **No Petri net:** if the project has a BPMN model but no Petri net, the coordinator generates one
  with the Petri net generator first.
- **Missing inputs:** it asks for what's missing and lists the process transition names and eFLINT
  acts/events to use when writing the rules.
- **Project state:** a pass or fail on a project in `MODEL_READY_FOR_VERIFICATION` moves it to
  `VERIFIED` or `VERIFICATION_FAILED`. In any other state the check is only recorded in the
  history. A new BPMN model discards the stored Petri net so it isn't reused out of date.

## Tests

29 offline tests (a fake of the service's job API, `tests/eflint_verifier/` and
`tests/test_coordinator_eflint.py`) plus 2 tests against the live service. Run them with the
project environment (`uv run pytest`, or `.venv/bin/python -m pytest`), not a system `pytest`. A
system Python lacks pydantic/httpx; the tests then skip with that reason, and with
`EFLINT_VERIFIER_LIVE=1` pytest stops with an error.

```sh
uv run pytest tests/eflint_verifier tests/test_coordinator_eflint.py    # offline
EFLINT_VERIFIER_LIVE=1 uv run pytest tests/eflint_verifier -v           # + live tests (service on :8100)
EFLINT_VERIFIER_LIVE=1 EFLINT_VERIFIER_KEEP_RUNS=1 uv run pytest tests/eflint_verifier/test_live_service.py -s
```

Tests write their run records to pytest's temporary directory, so normally nothing appears in
`storage/runs/`. With `EFLINT_VERIFIER_KEEP_RUNS=1` the live tests write to the agent's storage
(or `EFLINT_VERIFIER_RUNS_DIR`), and `-s` prints each run's folder.

## Limitations / next steps

Qualification rules are always required. The coordinator doesn't create them, and it doesn't yet
pass the output of `text_to_eflint` into verification, so the eFLINT spec has to be pasted too. A
natural next step would be an LLM step that drafts the rules and checks them with the service's
`validate` operation.
