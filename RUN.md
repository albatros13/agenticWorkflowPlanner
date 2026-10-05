# Running the agents

Secrets (API keys, `QDRANT_URL`/`QDRANT_API_KEY`, `OPENAI_API_KEY`, …) are read from the
repo-root `.env` at runtime.

## Option A — whole stack (one env for all agents)

Install everything and run any agent from the repo root:

```bash
uv sync --extra all          # all agents' runtime deps  (add --extra dev for tests)
# or: pip install -e '.[all]'

uv run uvicorn agents.text_to_bpmn.demo.main:app     --port 8080   # text -> BPMN
uv run uvicorn agents.text_to_eflint.demo.main:app   --port 8090   # text -> eFLINT
uv run uvicorn agents.coordinator.service:app        --port 8070   # coordinator
uv run uvicorn agents.regulatory_docs.demo.main:app  --port 8100   # regulatory docs (upload / find / vectorize to Qdrant)
```

You can also install a single agent's deps into the shared env with its extra, e.g.
`uv sync --extra regulatory_docs` (extras: `text_to_bpmn`, `text_to_eflint`, `coordinator`,
`regulatory_docs`; capability bundles: `llm`, `retrieval`, `ingestion`, `bpmn`, `eflint`).

## Option B — one agent on its own (isolated env per agent)

Each agent folder is its own project that installs only that agent's dependencies (the monorepo
is pulled in editable, so shared source stays importable). From the agent's directory:

```bash
cd agents/text_to_bpmn     && uv sync && uv run uvicorn agents.text_to_bpmn.demo.main:app    --port 8080
cd agents/text_to_eflint   && uv sync && uv run uvicorn agents.text_to_eflint.demo.main:app  --port 8090
cd agents/coordinator      && uv sync && uv run uvicorn agents.coordinator.service:app       --port 8070
cd agents/regulatory_docs  && uv sync && uv run uvicorn agents.regulatory_docs.demo.main:app --port 8100
```

`uv sync` builds a local `.venv/` (git-ignored) in that agent's folder. The per-agent dependency
set is defined once in the root `pyproject.toml` (the `text_to_bpmn` / `text_to_eflint` /
`coordinator` / `regulatory_docs` extras); each `agents/<agent>/pyproject.toml` just selects it.

## Tests

```bash
uv sync --extra dev
uv run pytest -q
```
