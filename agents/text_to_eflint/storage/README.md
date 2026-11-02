# text_to_eflint — private storage

This agent's **private** inputs and outputs. Not shared with other agents.

- `runs/<run_id>/` — one folder per `generate_eflint` call: the rendered modules
  (`Ontology.eflint`, `Norms.eflint`, `Init.eflint`, `Scenario-<n>.eflint`, and the self-contained
  `spec.eflint`), `spec.json` (the structured spec) and `report.json` (status, findings,
  scenario results, repair history). `run_id` is returned as `artifacts_ref`.

Location and backend are declared in [`config/storage.json`](../../../config/storage.json) under
`agents.text_to_eflint`. The `EFLINT_RUNS_DIR` env var overrides the runs location.

Contents are runtime data and are **gitignored**; only this README is tracked.
