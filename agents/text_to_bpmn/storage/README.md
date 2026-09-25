# text_to_bpmn — private storage

This agent's **private** inputs and outputs. Not shared with other agents.

- `runs/<run_id>/` — versioned pipeline artifacts (document, chunks, evidence, IR, terminology,
  BPMN, DMN) written by `ArtifactStore` for each generation run.

Location and backend are declared in [`config/storage.json`](../../../config/storage.json) under
`agents.text_to_bpmn`. Default backend is `local` (this folder); the `RUNS_DIR` env var overrides
the runs location. Swap `kind` for a cloud store later without touching agent code.

Contents are runtime data and are **gitignored**; only this README is tracked. For cross-agent
data use the shared area (`storage/`) instead.
