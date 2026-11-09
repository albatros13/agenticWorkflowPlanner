# Code review — agenticWorkflowPlanner

*Date: 2026-11-09 · Scope: `agents/` and `api/` (~13.3 k LOC, demo apps only spot-checked) · Commit `c99b55a`*

## How this review was done

- Ran the full test suite with coverage:
  `uv run --extra dev --with pytest-cov pytest --cov=agents --cov=api`.
  Result: **370 passed, 2 skipped, 1 failed**; total line coverage **88 %**.
- Static analysis: `ruff` (rule sets F, B, SIM, PLW, RUF005) and `vulture` (dead code, run on tracked files only).
- Read the coordinator, the contract, the LLM layer, the storage layer and each agent's handler by hand.
  I ran the suspected bugs in **§1** with short snippets to confirm them (marked *verified*).

Severity: **High** = wrong behaviour or data loss/exposure in a normal flow. **Medium** = wrong behaviour in an edge case, or a trap for whoever touches the code next. **Low** = cleanup.

---

## 1. Mistakes / bugs

### High

| # | Where | Problem | Failure scenario |
|---|---|---|---|
| B1 | `api/openai_client.py:5` | It imports `encode_image` from `content_provider`, but that module only defines `encode_image_with_type`. As a result **the OpenAI provider is broken**. | Any call to `OpenAIProvider.generate_json` raises `ImportError`. *Verified*: this is the one failing test, `test_openai_provider_delegates_to_api`. |
| B2 | `agents/text_to_bpmn/pipeline/llm/openai_provider.py:34-39` (same for `anthropic_provider.py`) | `except ImportError` assumes the SDK is missing, so B1 is reported as *"The 'openai' package is not installed"*. That hides the real cause. | With `openai` installed, the user is told to `pip install .[llm]`, which does not fix anything. |
| B3 | `agents/coordinator/intent.py:315-349` | `HeuristicIntentClassifier` matches **substrings** (`any(w in t …)`), not words, and the project-management rules run before the "process description" fallback. *Verified*:<br>• "The **special*ist*** reviews the claim…" → `list_projects`<br>• "The clerk records the **progress** of the application…" → `project_status`<br>• "At **check**out the customer pays; … consumer **regulat**ions" → `verify_eflint`<br>• "The manager may **delete** the draft order if…" → `delete_project` | Pasting a normal process description that contains "delete" or "remove project" into an open project **deletes the project**: `_delete` runs with the `project_id` the UI sends. Use word-boundary regexes, and only run the project commands on short, command-like messages (as `_classify_agent_command` already does with `_MAX_COMMAND_WORDS`). |
| B4 | `agents/coordinator/factory.py:31` | `_default_classifier` checks `os.getenv("ANTHROPIC_API_KEY")` **before** anything loads `.env`. `Settings.from_env()` → `load_dotenv()` only runs inside the `if`. | If the keys are only in `.env` (the documented setup), the coordinator quietly falls back to the heuristic classifier, which makes B3 the production path. Call `load_dotenv()` first. |
| B5 | `agents/coordinator/projects/json_store.py:22` (`_safe`) | `.` is allowed, so `_safe("..") == ".."`. `user_id` comes unauthenticated from `MessageRequest.user_id` and from the `?user_id=` query parameter. | *Verified*: `user_id=".."` resolves to the **parent** of the projects directory, so `GET /coordinator/projects?user_id=..` globs `*.json` outside the store. `_safe` also collides (`"a/b"` and `"a_b"` map to the same directory), so two users can share one store. Reject ids that do not match `^[A-Za-z0-9@._-]+$`, or contain `..`, or hash the id. |
| B6 | `agents/coordinator/factory.py:38` | `except Exception: pass` swallows every error while building the LLM classifier: a bad model name, a missing SDK, or B1. | The coordinator degrades silently to the heuristic classifier (see B3). At minimum, log a warning. |

### Medium

| # | Where | Problem |
|---|---|---|
| B7 | `coordinator.py:208-224` `_engineer` | With no project, `_create_project` persists the project (`store.create`) and `_apply("createDescription")` advances it **in memory only**. If the action is then refused, or no text→BPMN agent is registered, the method returns without `store.save`. The response reports `DESCRIPTION_DRAFTED`, but disk still has `INTENT_CAPTURED`. A `project_id` that is given but not found also silently creates a *new* project instead of reporting "not found". |
| B8 | `coordinator.py:864-869` `_derive_name` | It guards `(description or "")` and then calls `description.strip()` unguarded. *Verified*: `_derive_name(None)` raises `AttributeError`. |
| B9 | `coordinator.py:425` | `int(slots["max_states"])`: the LLM classifier's slots are not validated (`intent.py:452-455`), so a non-numeric value raises `ValueError` and the HTTP call returns 500. The LLM's `intent` is not checked against the enum either. If `generate_json` raises, `handle_message` has no `try` and the call also returns 500. |
| B10 | `coordinator.py:257-278` `_verify` | Once a `verify_model` agent is registered, the code still always refuses with "Verification agent not implemented", so the branch at 271-276 can never succeed. Either implement it or delete it and route through `_route_registered`. |
| B11 | `coordinator.py:495-513` `_missing_inputs_reply` | To build an *error message*, it makes a full `verify_eflint` call (`operation: validate`). That is a network round-trip to the external service, and it writes a run folder under `eflint_verifier/runs` on **every** "missing input" reply, which fills the run browser with noise. Use `pnml.normalize_pnml` / `pn_transitions` locally instead. |
| B12 | `regulatory_docs/vectorizer.py:76-81` | Point ids are `uuid5(doc_id:chunk_id)`. After re-vectorizing with a different `max_chars`, or a different chunker version, the old chunks with higher indexes stay in the collection, so search returns stale passages. "Idempotent" only holds if the chunking is identical. Delete by `doc_id` filter before the upsert. Also, if the collection exists with another dimension and `recreate_collection` is false, the Qdrant error only shows up at upsert time. |
| B13 | `text_to_eflint/agent.py:133` | `max_repair_rounds=int(p.get(...))`: the manifest schema says `maximum: 5`, but in-process callers are never validated against the schema. A request with `999` runs 999 LLM repair rounds (cost). Clamp it, as `_search` does for `limit`. |
| B14 | `coordinator/handoff.py:36-39, 59-60` | `_path` validates `filename` but not `project_id`, and `remove()` calls `shutil.rmtree(root / project_id)`. `JsonProjectStore` sanitises ids and `ModelHandoff` does not, so the two use different ids for the same project. It is safe today only because ids are generated uuids that must already exist in the store. Apply the same validation in both. |
| B15 | `coordinator/cache/__init__.py` + `cache/qdrant.py` | `SEMANTIC_CACHE=qdrant` is accepted, but every method raises `NotImplementedError`. With that setting, the first `new_project` or `find` returns 500. Reject the value in `build_cache` until the cache is implemented. |
| B16 | `text_to_bpmn/shaping.py:47` | `t.value_high and ('..'+…) or ''` drops a legitimate upper bound of `0`. Use `if t.value_high is not None`. |
| B17 | `text_to_eflint/generation/simulator.py:228-229` | `Exists`/`Forall` evaluate over `product(*domains)` with no size limit. A scenario with a few quantified variables over large fact sets can hang `validate_eflint`, which is called from the coordinator's request thread. |
| B18 | `regulatory_docs/demo/main.py:76-84` | `file.file.read()` loads the whole upload into memory with no size limit. Uploading the same bytes under a new name silently returns the old record and ignores the new title (`library._store` short-circuits). |
| B19 | `api/qdrant_remote_client.py` | It calls `print()` and `raise RuntimeError` **at import time** when `QDRANT_URL` is unset, and depends on `python-dotenv`, which is not in `pyproject.toml`. Nothing imports it (see §4). |
| B20 | `pyproject.toml` `[tool.setuptools.packages.find] include = ["agents*"]` | `api/` is not packaged, but the LLM providers import `api.anthropic_client` / `api.openai_client` lazily. In an installed wheel (the per-agent `pyproject.toml` deployments), every real LLM call fails with an `ImportError`, which is then misreported as in B2. |

### Low

- `syntax.py:120`: `KEYWORDS` lists `"Event"` twice (ruff B033).
- `coordinator/service.py:62, 72` and the demo apps: `raise HTTPException` inside `except` without `from exc` (B904).
- `zip()` without `strict=True` at `vectorizer.py:81`, `in_memory.py:51`, `checks.py:205`, `simulator.py:141,144,169,228`. A length mismatch is silently truncated. The vectorizer checks the lengths first; the others do not.
- `simulator.py:169`: the loop variable `var` is unused.
- `regulatory_docs/qdrant_store.py:64`: the loop variable `payload` is overwritten.
- `contract/registry.py:249-250`: two config entries claiming the same intent → the later one silently wins. Raise `RegistryError` instead.

---

## 2. Redundancies (duplicated logic)

| # | Duplication | Suggested single home |
|---|---|---|
| R1 | **Five `service.py` files** (`text_to_bpmn`, `text_to_eflint`, `petri_net_generator`, `eflint_verifier`, plus coordinator `/health`) repeat the same `health` / `manifest` / `invoke_sync` / `invoke` / `jobs/{id}` endpoints and the in-memory `_jobs` dict line for line. | `agents/contract/service.py: make_agent_app(handler, title) -> FastAPI`. Each service adds only its typed endpoint. |
| R2 | `_default_provider()` is copied in `petri_net_generator/agent.py:32` and `text_to_eflint/agent.py:72`, and again inline in `coordinator/factory.py:33-36`. | One `agents/text_to_bpmn/shaping.default_provider()`. |
| R3 | The **runs-folder pattern**: `_runs()` (env override → `agent_storage(...).path("runs")`), `_RUN_ID` regex, `list_runs()` / `load_run()` and `_read_json` are reimplemented in `text_to_bpmn/runs.py`, `text_to_eflint/agent.py`, `eflint_verifier/agent.py` and `petri_net_generator/agent.py`. | A small `agents/runs.py` (`RunStore(agent_name, env_var)`). |
| R4 | Resolving the regulatory-docs library root (`REGDOCS_DIR` or `agent_storage("regulatory_docs")`) is duplicated in `regulatory_docs/agent.py:294-299` and `text_to_eflint/agent.py:187-191`. | `regulatory_docs.library.default_library()`. |
| R5 | The `"validation": {bpmn_valid, bpmn_errors, dmn_valid, connectivity_*}` block is built identically in `text_to_bpmn/agent.py:119-135` and `runs.py:432-447`. | `shaping.build_validation(bpmn_xml, dmn_xml)`. |
| R6 | `Coordinator._ok`, `_refused` and `_reply` build the same `CoordinatorResponse`. `_refused` equals `_reply(needs_input=True)`, and `_ok` equals `_reply`. | Keep only `_reply`. |
| R7 | Project deletion is implemented twice: `Coordinator._delete` and `coordinator/service.py:67-79` (`delete_project`). The copies can drift (one already ignores the transition result). | Make the endpoint call a public `Coordinator.delete_project()`. |
| R8 | The same "record history + append run + save" tail is repeated in `_petri_net`, `_verify_eflint`, `_generate_eflint`, `_route_registered`, `_record_docs`. | `_record(project, intent, result, summary)`. |
| R9 | Model defaults are set in four places and disagree: `Settings.openai_model="gpt-4o"`, `openai_provider.DEFAULT_MODEL="gpt-5"`, `api/openai_client.DEFAULT_OPENAI="gpt-5"`; Anthropic `"claude-sonnet-4-6"` appears three times. The provider reads `ANTHROPIC_MODEL` at import, and so does `Settings`. | Only `Settings` should hold defaults. Consider moving the default to a current model (e.g. `claude-sonnet-5-5`). |
| R10 | `Settings.runs_dir` (`config.py:71,82`) duplicates `text_to_bpmn.runs.runs_dir()` and is **never used**: runs go to `storage/…`, not `REPO_ROOT/runs`. | Remove it. |
| R11 | `select_provider` (`shaping.py:224-235`): both branches call `get_provider`, and the only effect is mapping `mock`/unknown to `auto`. `get_provider` has its own `mock` error that can therefore never trigger from this path. | Collapse into `get_provider(…, provider=name if name in {...} else "auto")`. |
| R12 | `coordinator.py:390-391` resets `pnml = None` when a `.pn` is attached. That is redundant: saving a `.pn` attachment has already discarded the stored PNML (line 374), and without a store `pick("pnml")` only returns attachments. | Remove those lines. |
| R13 | `api/anthropic_client.py` and `api/openai_client.py` each contain about six chat / image / HTML / tool-loop helpers, carried over from another project and mirrored between the two providers. Only `ask_*_structured` is used (§4). | Delete the rest, or move it into a separate legacy module. |

---

## 3. Non-optimal code (performance and design)

| # | Where | Issue |
|---|---|---|
| P1 | `coordinator/service.py:26` | `build_coordinator()` runs at **import** time, so importing the module for tests or tooling builds every handler and reads every config file. Use a FastAPI lifespan or a lazy getter. |
| P2 | `projects/store.py:33-45` `search` | `list()` parses every project JSON, then `get()` parses each one **again**: O(2n) file reads per fallback search. Filter inside `list` on the already-loaded `Project`s. |
| P3 | `projects/json_store.py:35-38` `_user_dir` | It `mkdir`s on every read (`get`, `list`), so read-only calls with arbitrary `user_id`s create directories (combines with B5). |
| P4 | `regulatory_docs/agent.py` `_search`, `_list_vectorized`, `_vectorize` | Every request builds a new Qdrant client (`_store_factory`), which means a new TLS connection to the cloud cluster, and a new OpenAI client. `_search` and `_list_vectorized` also scan the whole library (`find()` reads every `meta.json`) just to set `in_library`. Cache clients per profile, and keep an id set in `DocumentLibrary`. |
| P5 | `text_to_bpmn/pipeline/pipeline/runner.py:44,77` | `InMemoryEvidenceIndex` is filled on every run and **never queried**: wasted work, and misleading to readers. Either use it in `EvidenceExtractor` or remove it. |
| P6 | `runner.py` stages | Each stage re-reads and re-validates the previous stage's artifact from disk (`store.require` → `json.loads` → `model_validate`), even inside a single in-process run. Fine for resumability, but pass the objects through when they are already in memory. `TerminologyResolver.from_config()` is also re-read on every request (`Pipeline.__init__`). |
| P7 | `coordinator.py` | `self.machine.allowed_actions(state)` rebuilds the merged action list on each call, and is called 2-3 times per response (`can()` calls it too). This is cheap, but precomputing it per state in `StateMachine.__init__` removes the repeated work. |
| P8 | `coordinator.py:130` | `self.registry.intents()` sorts the intent map on every unmatched message. Cache a `frozenset`. |
| P9 | `simulator.py:221-223` | The `Cmp` evaluation builds dicts that evaluate **all** four ordering comparisons to pick one. Use `operator` lookups instead (this is about readability as much as speed). |
| P10 | Agent `service.py` `invoke` (async path) | It runs the handler synchronously and keeps every `Job` in an unbounded `_jobs` dict, so memory grows for the life of the process, and the "async" API is not actually async. |
| P11 | `api/*_client.py` | They create SDK clients and read keys **at import time** (`llm_client = Anthropic(api_key=...)`). If `.env` is loaded later, the module keeps `None`. `anthropic_client` also uses `print` for warnings, and `logging.basicConfig` at import changes the host application's logging configuration. |

---

## 4. Dead code (never called from the code or the tests)

| Symbol | Location |
|---|---|
| `ask_anthropic_text_llm`, `ask_anthropic_chat`, `run_anthropic_tool_loop`, `get_image_description_from_file`, `ask_anthropic_llm`, `ask_anthropic_llm_html` | `api/anthropic_client.py:66-289` |
| `ask_openai_text_llm`, `ask_openai_chat`, `run_openai_tool_loop`, `get_image_description_from_file`, `ask_openai_llm`, `ask_openai_llm_html` (these also call the missing `encode_image`) | `api/openai_client.py:61-265` |
| whole module (`get_remote_client`, `get_kg_client`) | `api/qdrant_remote_client.py` |
| `get_html_content`, `get_full_paths`, `encode_image_with_type` (only used by the dead helpers above) | `text_to_bpmn/pipeline/content_provider.py` |
| `QdrantEvidenceIndex` (0 % coverage; imports qdrant at call time) and `all_chunks()` on all three indexes | `text_to_bpmn/pipeline/retrieval/*` |
| `Pipeline.run_through_bpmn` | `runner.py:125` |
| `StateMachine.is_terminal`, `StateMachine._spec` | `coordinator/state/machine.py:39,76` |
| `EflintVerifierClient.submit`, `.status` (only `run` is used) | `eflint_verifier/client.py:91-97` |
| `_strip_comments` | `eflint_verifier/agent.py:317` |
| `LLMProvider._loads` | `llm/base.py:52` |
| `redact` (secrets are "never logged", but nothing logs through it) | `pipeline/config.py:55` |
| `Document.section_paths` | `ingestion/base.py:48` |
| `ATTACHMENT_KINDS` | `coordinator/intent.py:65` |
| `min_score` parameter (never passed, never used by any implementation) | `coordinator/cache/*.py` |
| `QdrantSemanticCache._url`, `_collection`, `_embed` (skeleton) | `coordinator/cache/qdrant.py` |
| Unused imports: `json` (`json_store.py:12`, `library.py:17`), `DMN` (`dmn/compiler.py:14`), `field` (`llm/mock.py:10`), `_Graph` (`ontology/loader.py:19`) | ruff F401 |

---

## 5. Untested or weakly tested code

From the coverage run. 100 %-covered files are omitted, and so are lines that are only `pragma`/`main()` entry points.

| Module | Cover | What is not exercised |
|---|---|---|
| `api/openai_client.py` | **5 %** | everything; the one test that reaches it fails (B1) |
| `api/anthropic_client.py` | **23 %** | `ask_anthropic_structured` only via a mock; the `max_tokens` truncation branch and the no-`tool_use` branch are not covered |
| `api/qdrant_remote_client.py` | 0 % | dead module |
| `eflint_verifier/service.py` | **0 %** | no TestClient test at all (the typed `/verify` 200/502 mapping is untested) |
| `coordinator/cache/qdrant.py`, `cache/__init__.py` | 0 % / 64 % | `build_cache("qdrant")` and the unknown-kind error |
| `text_to_bpmn/pipeline/retrieval/qdrant_index.py` | 0 % | dead |
| `text_to_bpmn/pipeline/ingestion/pdf_loader.py` | **30 %** | PDF loading is untested, although `regulatory_docs` accepts PDF uploads |
| `text_to_bpmn/pipeline/content_provider.py` | 36 % | dead helpers |
| `regulatory_docs/embeddings.py` | 74 % | `_openai_embedder` (batching, the `dimensions` kwarg for `text-embedding-3-*`, missing key) |
| `text_to_bpmn/pipeline/llm/openai_provider.py` | 69 % | error normalisation |
| `text_to_bpmn/service.py`, `text_to_eflint/service.py`, `petri_net_generator/service.py` | 73-79 % | the async `invoke` / `jobs/{id}` endpoints and the typed endpoints' error codes (422/500/502) |
| `contract/base.py` | 75 % | `HttpAgent.manifest` failure normalisation and `HttpAgent.invoke` transport-error → `AgentResult(failed)` |
| `text_to_eflint/generation/simulator.py` | **79 %** | quantifier evaluation (`Exists`/`Forall`, 219-231), comparison type errors, `enabled`/`violated` error paths, `postulate` of an act |
| `text_to_eflint/labelling/frame_templates.py` | 73 % | template lookups for unknown frames |
| `text_to_bpmn/pipeline/bpmn/model.py` | 86 % | graph building for several gateway/boundary branches (111-115, 191-238) |
| `coordinator/coordinator.py` | 87 % | `_find` fallback (145-152), `_new_project` similar-project prompt (155-171), `_delete` with handoff, `_engineer` agent-missing/failure branches (224, 235-237), `_generate_pnml` failure mapping (484-488), many `_docs_unavailable` branches |
| `text_to_eflint/generation/checks.py` | 91 % | 171-185 (a whole check block) |
| `text_to_bpmn/pipeline/config.py` | 82 % | `load_dotenv` parsing (quotes, `override`, comments) |

**Behaviour gaps not covered by any test**, even where the lines are covered:

- No negative tests for the heuristic classifier on process descriptions that contain command words (B3). Adding the four sentences from B3 as regression cases would have caught it.
- No test for path-like `user_id` / `project_id` / `doc_id` values (B5, B14; `library.get`/`load_document` also join an unvalidated `doc_id`).
- No test that `build_coordinator()` picks the LLM classifier when the key is only in `.env` (B4).
- No test that re-vectorizing a changed document removes stale chunks (B12).
- No packaging test: `pip install .` followed by a provider call would catch B20.
- `tests/eflint_verifier/test_live_service.py` and `tests/regulatory_docs/test_cloud_qdrant.py` talk to real services. They pass locally but warn *"Qdrant client 1.15.1 is incompatible with server 1.20.0"*. Mark them (e.g. `@pytest.mark.live`) so CI can skip them.

---

## 6. Hygiene and documentation drift

- **Stale references.** `todo/COORDINATOR_PLAN.md` is cited in `config/agents.json`, `paths.py`, `contract/*`, `coordinator/*`, `text_to_bpmn/*`, but the file no longer exists anywhere in the repo. Docstrings also still point to `src/…` (`src.llm.base`, `src/retrieval/qdrant_index.py`, "the pipeline code in `src/` is not modified").
- `content_provider.py` says it exists "to satisfy the import in `api/anthropic_client.py`". It only exists for dead code (§4).
- A commented-out block is left in `api/anthropic_client.py:17-22`.
- `agents/petri_net_generator/.venv` sits inside the source tree. It is git-ignored, but tools that do not read `.gitignore` (vulture, IDE indexers, `find`) scan it.
- `tests/__pycache__` contains `.pyc` files for tests that no longer exist (`test_api`, `test_bpmn_compiler`, … at the top level), and the traceback paths point to `/home/natal/PycharmProjects/AGENTS/…`, a stale copy. Run `find . -name __pycache__ -exec rm -rf {} +`.
- `ProviderX(api_key=...)` accepts a key and ignores it (`anthropic_provider.py:21`, `openai_provider.py:21`). Either honour it or remove the parameter.
- ruff reports 98 findings, 54 of them auto-fixable. Adding a `[tool.ruff]` section and a pre-commit hook would keep that from piling up again.

---

## 7. Suggested order of work

1. **B1 + B2 + B20**: fix the OpenAI import, stop masking `ImportError`, and package `api/` (or move the SDK calls into `agents/…/llm/`). This repairs the failing test and the OpenAI path.
2. **B3 + B4 + B6**: load `.env` before choosing the classifier, log fallbacks, and switch the heuristic rules to word boundaries plus a "short command" guard. Add the B3 sentences as regression tests. This removes the "description deletes my project" failure.
3. **B5 + B14**: strict id validation in `JsonProjectStore`, `ModelHandoff` and `DocumentLibrary`.
4. **B7, B9, B11, B12, B13**: coordinator state consistency, slot validation, no remote call while building an error message, stale-chunk cleanup, clamping the repair rounds.
5. **R1-R8**: one shared agent-service factory, one runs helper, one provider default, and a single `_reply`/`_record` in the coordinator.
6. Delete the dead code in §4, which also removes the three lowest-coverage modules.
7. Add the missing tests listed in §5, especially `eflint_verifier/service.py`, `pdf_loader`, the simulator quantifiers and the `HttpAgent` error paths.
