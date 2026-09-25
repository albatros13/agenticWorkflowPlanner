# coordinator — private storage

This agent's **private** inputs and outputs. Not shared with other agents.

- `projects/<user_id>/<project_id>.json` — persisted user projects (state, run references,
  history) written by `JsonProjectStore`.

Location and backend are declared in [`config/storage.json`](../../../config/storage.json) under
`agents.coordinator`. Default backend is `local` (this folder); the `PROJECTS_DIR` env var
overrides the projects location. Swap `kind` for a database (Postgres/Neo4j) later without
touching agent code.

Contents are runtime data and are **gitignored**; only this README is tracked. For cross-agent
data use the shared area (`storage/`) instead.
