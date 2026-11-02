# petri_net_generator — private storage

This agent's **private** inputs and outputs. Not shared with other agents.

- `runs/<run_id>/` — one folder per generation: `net.pnml`, `response.json` (summary, markings,
  validation findings; PNML/DOT omitted), and `ir.json` for the text / IR input paths.
  `run_id` is returned as `pnml_ref` / `artifacts_ref`.

Location and backend are declared in [`config/storage.json`](../../../config/storage.json) under
`agents.petri_net_generator`. The `PETRI_NET_RUNS_DIR` env var overrides the runs location.

Contents are runtime data and are **gitignored**; only this README is tracked.
