# coordinator — the natural-language entry point

The coordinator is the single agent the user talks to. For every message it:

1. **recognises the intent** and extracts slots (structured LLM output, or a keyword heuristic
   when no API key is set);
2. **resolves the project**: new, open, or find similar (through the semantic cache);
3. **gates the action** through the project lifecycle state machine. A request that the current
   state forbids is *refused*, not executed;
4. **routes** the work to a sub-agent through the `AgentRegistry` (in-process or over HTTP, from
   `config/agents.json`);
5. **applies the state transition**, records the history, and persists the project.

The LLM only classifies; Python decides the control flow. Documents pasted into a message (a BPMN
model, or eFLINT-verification inputs in fenced code blocks) are split off **in code** before
classification and forwarded verbatim, never echoed through the LLM.

## Intents

| Intent | What happens | Sub-agent |
| --- | --- | --- |
| `new_project` | Creates a project; with a description it moves to `DESCRIPTION_DRAFTED`. Similar projects are offered first. | — |
| `engineer_process` | Models a description as BPMN, or refines the model (`createModel` / `refineModel`). Without a project, one is created from the description. The BPMN is copied to the shared hand-off area. | `text_to_bpmn` |
| `generate_petri_net` | Builds a workflow Petri net. Source order: attached BPMN → description in the message (`Petri net for: …`) → the project's BPMN model → the project's description. A derived view: recorded in the history, no state change. Blocking ambiguities come back as questions. | `petri_net_generator` |
| `verify_eflint` | Verifies the process against an eFLINT specification (see below). | `eflint_verifier` (+ `petri_net_generator`) |
| `verify_model` | Delegates to `verify_eflint` when the project has an eFLINT spec; otherwise it is state-gated and reports that no verification agent is available. | — |
| `open_project`, `project_status`, `list_projects`, `find_project`, `revise_description`, `delete_project` | Project management. `revise_description` with a semantic change invalidates prior verification. | — |

The `regulatory_docs` and `text_to_eflint` agents are registered too, but the intent classifier
does not emit their intents yet.

### eFLINT verification

Triggered by verify / check / comply wording together with eFLINT / norm / regulation, or by an
attached eFLINT block. A search bound is read from phrases like "bound 12" or "within 12 steps".

- **Inputs** are pasted as fenced code blocks: ```` ```eflint ````, ```` ```qualification ````
  (rules mapping process steps to eFLINT acts/events), optionally ```` ```analysis ```` and
  ```` ```pnml ````/```` ```pn ````. They are saved in the project's hand-off folder, so
  "verify again" needs no re-upload.
- **No Petri net:** if the project has a BPMN model, the Petri net generator is called first.
- **Missing inputs:** the reply says what is missing and lists the process transition names and
  eFLINT acts/events to use in the rules.
- **Lifecycle:** a pass or fail on a project in `MODEL_READY_FOR_VERIFICATION` moves it to
  `VERIFIED` or `VERIFICATION_FAILED`. In any other state the check is only recorded in the
  history.

See [`agents/eflint_verifier/README.md`](../eflint_verifier/README.md).

## Project lifecycle

States and allowed actions come from `config/project-states.json`:

```
INTENT_CAPTURED → DESCRIPTION_DRAFTED → MODEL_IN_PROGRESS → MODEL_READY_FOR_VERIFICATION
  → VERIFICATION_IN_PROGRESS → VERIFIED → IMPLEMENTATION_GENERATED → TESTING
  → READY_FOR_DEVELOPMENT → DEPLOYED → RETIRED
```

`deleteProject` and `reviseDescription` apply from any state. Every response reports the current
`state` and its `allowed_actions`. See `todo/coordinator/PROJECT_STATE_MANAGEMENT.md`.

## Code layout

| Path | Role |
| --- | --- |
| `intent.py` | Intents, `HeuristicIntentClassifier`, `LLMIntentClassifier` (structured output), and in-code extraction of pasted BPMN / verification artifacts. |
| `coordinator.py` | Orchestration: intent → project → state gate → route → persist. |
| `factory.py` | `build_coordinator()`: wires the handlers, registry, state machine, store, cache and classifier from config and environment. |
| `service.py` | FastAPI app and chat UI. |
| `handoff.py` | `ModelHandoff`: per-project artifacts in the shared area (`storage/projects/<id>/`): `model.bpmn`, `model.pnml`, `model.pn`, `spec.eflint`, `rules.qr`, `checks.analysis`. A new BPMN model discards the stale `model.pnml`. |
| `projects/` | `Project` model and the pluggable `ProjectStore` (JSON on disk by default). |
| `state/` | Data-driven state machine. |
| `cache/` | `SemanticProjectCache`: a no-op default plus a Qdrant skeleton (deferred). |
| `static/index.html` | Chat UI. |

## HTTP API and chat UI

```bash
uv run uvicorn agents.coordinator.service:app --port 8000   # port from COORDINATOR_PORT when run as a module
```

Open **http://localhost:8000/** for the chat UI. It has a chat window, a project sidebar
(select / delete / new) with lifecycle states, inline BPMN rendering with validation badges and a
download button, Petri net badges with a **Download .pnml** button, and the active project's
`allowed_actions` as chips.

| Endpoint | Purpose |
| --- | --- |
| `POST /coordinator/message` | Main entry point: `{text, user_id?, project_id?}` → `{reply, intent, project, state, allowed_actions, agent_result, similar, needs_input}`. |
| `GET /coordinator/projects?user_id=` | List a user's projects. |
| `GET /coordinator/projects/{id}?user_id=` | Project detail: state, runs, history. |
| `DELETE /coordinator/projects/{id}?user_id=` | Delete a project and its hand-off folder. |
| `GET /health` | Health check. |

```bash
curl -s localhost:8000/coordinator/message -H 'content-type: application/json' -d '{
  "text": "The family physician assesses the IPSS. If improvement is at least 25% continue treatment, otherwise refer back to the urologist.",
  "user_id": "you@example.com"
}'
```

## Configuration

| Var | Default | Purpose |
| --- | --- | --- |
| `AGENTS_CONFIG` | `config/agents.json` | Agent registry. |
| `PROJECT_STATES_CONFIG` | `config/project-states.json` | Lifecycle spec. |
| `PROJECTS_DIR` | `agents/coordinator/storage/projects` | Project store location. |
| `HANDOFF_DIR` | `storage/projects` | Shared per-project artifacts. |
| `SEMANTIC_CACHE` | `noop` | `noop` \| `qdrant` (qdrant deferred). |
| `DEFAULT_USER_ID` | `default` | User when a request has no `user_id`. |
| `COORDINATOR_PORT` | `8000` | Port when started with `python -m agents.coordinator.service`. |

The LLM classifier is used when `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` is set; otherwise the
heuristic classifier runs fully offline.

## Storage

Projects are saved under `storage/projects/<user_id>/<project_id>.json` (see
[`storage/README.md`](storage/README.md)).

## Tests

`tests/coordinator/` (state machine, project store), `tests/test_coordinator.py`,
`tests/test_coordinator_service.py`, `tests/test_coordinator_petri_net.py` and
`tests/test_coordinator_eflint.py`, all offline.
