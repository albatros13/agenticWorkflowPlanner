# Code review — fixes

*Date: 2026-11-09 · Follows up [`docs/planning-instructions/CODE-REVIEW.md`](../planning-instructions/CODE-REVIEW.md). The item ids (B1…, R1…, P1…) refer to that review.*

## Result

| | Before | After |
|---|---|---|
| Tests | 370 passed, **1 failed**, 2 skipped | **441 passed**, 0 failed, 2 skipped |
| Line coverage (`agents`, `api`) | 88 % | **93 %** |
| ruff findings (F, B, SIM, PLW, RUF005 on `agents`, `api`) | 98 | 9 (style only: SIM105/SIM102/RUF005, one `global`, one intentional non-strict `zip`) |
| Source size (`agents`, `api`, `tests`, config) | — | 71 files changed, +706 / −1313 lines |

How it was checked: `uv run --extra dev --with pytest-cov pytest --cov=agents --cov=api`.
The run includes the live tests (`test_cloud_qdrant.py`, `test_live_service.py`). The live Qdrant test found a real requirement of one fix (see B12).

Nothing has been committed. The index still has the `docs/todo/*` "AD" entries it had before this work; I didn't touch them.

---

## 1. Bugs fixed

### High

| # | Fix | Where | Test |
|---|---|---|---|
| **B1** | `api/openai_client.py` no longer imports the non-existent `encode_image`. The module now contains only `ask_openai_structured`. | `api/openai_client.py` | `test_openai_provider_delegates_to_api` (was the failing test) |
| **B2** | New `import_api_function()` reports "*<sdk>* package is not installed" **only** when the SDK module itself is missing. A broken or missing `api` module is reported as "Cannot load the … client …". | `pipeline/llm/base.py`, `anthropic_provider.py`, `openai_provider.py` | `test_broken_api_module_is_not_reported_as_a_missing_sdk`, `test_provider_without_sdk_raises_llm_error` (rewritten so it no longer depends on import order) |
| **B3** | Rewrote the heuristic classifier: **whole-word** regexes (`_phrase`, `_VERIFY`, `_PETRI`), and project/verification commands only fire for command-length messages (≤ 20 words before the colon). Deletion requires an explicit object (`delete/remove/discard/drop [this/the/my…] project`). "List" and "status" are anchored command forms. | `coordinator/intent.py` | `test_process_descriptions_are_not_taken_for_commands` (the four sentences from the review plus a long mixed one), `test_short_commands_still_work`, `test_a_description_mentioning_delete_does_not_delete_the_open_project` |
| **B4** | `_default_classifier` calls `load_dotenv()` **before** looking for an API key. | `coordinator/factory.py` | `test_factory_reads_the_key_from_dotenv` |
| **B5** | Ids are **validated, not rewritten**. The new `agents.storage.safe_id()` / `is_safe_id()` accept `[A-Za-z0-9@._+-]{1,128}`, with no leading dot and no `..`. `JsonProjectStore` raises `ValueError` for a bad user id; a bad project id is "not found". The coordinator service answers **400** for an unsafe `user_id`. Distinct ids can no longer collide. | `agents/storage.py`, `projects/json_store.py`, `coordinator/service.py` | `test_unsafe_user_ids_are_rejected`, `test_distinct_user_ids_no_longer_collide`, `test_unsafe_project_id_is_not_found`, `test_service_rejects_unsafe_user_ids`, `test_safe_ids_pass` / `test_unsafe_ids_fail` |
| **B6** | If the LLM classifier cannot be built, the coordinator logs a warning with the cause instead of silently swallowing it. A missing key is logged at info level. | `coordinator/factory.py` | `test_factory_logs_and_falls_back_when_the_llm_classifier_cannot_be_built` |

### Medium

| # | Fix | Where | Test |
|---|---|---|---|
| **B7** | An implicit project is **saved right after** `createDescription`, before any early return. A `project_id` that is given but not found now answers "couldn't find that project" instead of silently creating a new project. | `coordinator.py` `_engineer` | `test_implicit_project_is_saved_even_when_no_bpmn_agent_is_available`, `test_unknown_project_id_is_reported_not_replaced` |
| **B8** | `_derive_name(None)` returns "Untitled project". | `coordinator.py` | `test_derive_name_accepts_none` |
| **B9** | `LLMIntentClassifier` treats the model's answer as untrusted. An intent outside the offered enum becomes `unknown`; slots are coerced to their schema type or dropped (`_typed_slots`); a provider failure falls back to the heuristic classifier. The coordinator only forwards a positive-int `max_states`. | `coordinator/intent.py`, `coordinator.py` | `test_llm_intent_outside_the_offered_set_becomes_unknown`, `test_llm_slots_of_the_wrong_type_are_dropped_or_coerced`, `test_llm_failure_falls_back_to_the_heuristic_classifier` |
| **B10** | `_verify` now calls a registered `verify_model` agent, passing the description and the project's BPMN. A `passed`/`failed` verdict drives `startVerification → completeVerification{Success,Failure}`; anything else is recorded as an inconclusive run. | `coordinator.py` | `test_verify_model_agent_drives_the_lifecycle` ×2, `test_inconclusive_verification_keeps_the_state`, `test_failed_agent_result_is_recorded_in_the_project` |
| **B11** | The "missing inputs" reply computes the Petri net transition names **locally** (`pnml.normalize_pnml` / `pn_transitions`). It no longer calls the remote service or writes a verifier run. | `coordinator.py` `_transition_names` | `test_missing_inputs_are_requested_with_helpful_names`, updated to assert `service.submitted == []` |
| **B12** | Before upserting, the vectorizer deletes the document's old chunks (`VectorStore.delete_document`, a filtered delete on `doc_id`). `ensure_collection` creates a **keyword payload index on `doc_id`**. The live cloud test showed Qdrant Cloud rejects the filtered delete without that index; the index call is idempotent, so existing collections are upgraded on first use. | `regulatory_docs/vectorizer.py`, `qdrant_store.py`, `tests/regulatory_docs/conftest.py` (the fake store) | `test_revectorizing_with_other_chunking_leaves_no_stale_chunks`, `test_cloud_qdrant.py` (live) |
| **B13** | `max_repair_rounds` is clamped to `[0, MAX_REPAIR_ROUNDS=5]`, matching the manifest schema. A non-integer value is a `ValueError`, which surfaces as a failed result. | `text_to_eflint/agent.py` | `test_repair_rounds_are_clamped` |
| **B14** | `ModelHandoff` validates `project_id` and the artifact name with `safe_id`, so `remove("../x")` can no longer `rmtree` outside its root. | `coordinator/handoff.py` | `test_handoff_rejects_unsafe_project_ids` |
| **B15** | `build_cache("qdrant")` fails at start-up with "not implemented yet", instead of returning a skeleton that raises on the first request. | `coordinator/cache/__init__.py` | `test_unimplemented_qdrant_cache_is_refused_at_start_up` |
| **B16** | A threshold whose upper bound is `0` is no longer dropped (`is None` instead of a truthiness check). | `text_to_bpmn/shaping.py` | `test_threshold_upper_bound_of_zero_is_kept` |
| **B17** | `Exists`/`Forall` refuse more than `MAX_QUANTIFIER_BINDINGS = 100 000` bindings with an `EvalError` instead of hanging. | `text_to_eflint/generation/simulator.py` | `test_quantifier_bindings_are_bounded` |
| **B18** | The demo file upload reads at most `REGDOCS_MAX_UPLOAD_BYTES` (default 25 MiB) and answers **413** above that. | `regulatory_docs/demo/main.py` | `test_demo_upload_is_size_limited` |
| **B19** | Deleted `api/qdrant_remote_client.py`, which had side effects at import, an undeclared dependency and no callers. | — | — |
| **B20** | `pyproject.toml` packages `api*` as well as `agents*`, so an installed agent can make LLM calls. | `pyproject.toml` | — (no wheel-install test yet; see §5) |

### Low

- `KEYWORDS` no longer lists `"Event"` twice (`syntax.py`).
- `raise HTTPException(...) from exc` in the coordinator service and the three demo apps (B904).
- `zip(..., strict=True)` where the lengths must match (vectorizer, in-memory index, simulator `_construct`/quantifiers). `strict=False` is now explicit where the shorter sequence is intended (`checks.py`, after an arity error has been reported).
- Unused loop variable (`simulator.py`) and the overwritten loop variable (`qdrant_store.summarize_documents`) are fixed.
- **Registry:** an intent claimed by two config entries raises `RegistryError` instead of the later entry winning silently (`test_an_intent_claimed_by_two_agents_is_a_config_error`).
- An intent that is registered but whose agent is not loaded now gets a polite reply instead of an `AttributeError` on `None` (`test_route_registered_without_a_loaded_agent_answers_politely`).

---

## 2. Redundancies removed

| # | Change |
|---|---|
| **R1** | New `agents/contract/service.py: make_agent_app(title, get_handler)` serves `/health`, `/agent/manifest`, `/agent/invoke_sync`, `/agent/invoke`, `/agent/jobs/{id}`. The four agent `service.py` files now hold only their typed endpoints. The handler is looked up per request, so the existing tests that monkeypatch `svc._handler` keep working. |
| **R2** | One `shaping.default_provider()` replaces the copies in `petri_net_generator/agent.py`, `text_to_eflint/agent.py` and `coordinator/factory.py`. |
| **R3** | New `agents/runs.py` (`RUN_ID`, `is_run_id`, `new_run_id`, `runs_root`, `read_json`) replaces four copies of the run-id regex, the `_runs()` resolution logic and `_read_json`. |
| **R4** | `regulatory_docs.library.default_library_root()` is the one definition of the library location (`REGDOCS_DIR` or private storage), used by both regulatory_docs and text_to_eflint. |
| **R5** | `shaping.build_validation()` replaces the duplicated validation block in `text_to_bpmn/agent.py` and `runs.py`. |
| **R6** | `_ok` and `_refused` are now one-line wrappers around `_reply`, so one code path builds every `CoordinatorResponse`. |
| **R7** | `Coordinator.delete_project()` is the single deletion path, used by the chat intent and by `DELETE /coordinator/projects/{id}` (`test_service_delete_uses_the_coordinator`). |
| **R8** | `Coordinator._record()` replaces the repeated "append run + history + save" tails, and `_invoke()` is used for every agent call (Petri net, eFLINT verification, BPMN, PNML generation). This removed six hand-built `AgentRequest`s. |
| **R9** | Default models live only in `pipeline/config.py` (`DEFAULT_ANTHROPIC_MODEL`, `DEFAULT_OPENAI_MODEL`). The providers and `api/` clients take the model from the caller. The effective defaults are unchanged (`claude-sonnet-4-6`, `gpt-4o`); see §4. |
| **R10** | Removed the unused `Settings.runs_dir`. |
| **R11** | `select_provider` is reduced to one `get_provider` call. |
| **R12** | Removed the redundant `pnml = None` reset in `_verify_eflint`. |
| **R13** | `api/anthropic_client.py` (289 → 57 lines) and `api/openai_client.py` (265 → 60 lines) keep only the structured-output calls that are actually used. |

---

## 3. Performance and design changes

| # | Change |
|---|---|
| **P1** | The coordinator service builds the `Coordinator` on first request (`_get()`), not at import time. |
| **P2** | `JsonProjectStore.search` reads each project file once (it used to read each file twice). |
| **P3** | Reads (`get`, `list`) no longer create user folders; the folder is created on the first write (`test_reads_do_not_create_user_folders`). |
| **P4** | `RegulatoryDocsHandler` reuses its embedder and Qdrant client per profile section. `in_library` flags come from `DocumentLibrary.ids()` (folder names, no JSON parsing) instead of `find()` (`test_regulatory_agent_reuses_clients_per_profile`). |
| **P5** | Removed the `InMemoryEvidenceIndex` that `Pipeline` filled on every run but never queried. The class itself stays; its retrieval tests use it. |
| **P6** (part) | The terminology resolver config is parsed once per process (`_default_resolver`, an `lru_cache`). |
| **P7** | `StateMachine` precomputes each state's allowed actions. |
| **P8** | The coordinator caches its registered intents as a `frozenset`. |
| **P9** | The simulator's comparisons use an `operator` table instead of evaluating every comparison. |
| **P10** | The agent job table is bounded (`JobTable`, oldest dropped beyond 1 000) (`test_job_table_is_bounded`). |
| **P11** | The `api/` clients have no import-time side effects (no SDK client, no `print`, no `logging.basicConfig`). The key is read on each call, after `.env` is loaded, and one SDK client is cached per key (`test_api_clients_import_without_side_effects`). |

---

## 4. Dead code removed

- `api/qdrant_remote_client.py` and `agents/text_to_bpmn/pipeline/content_provider.py`, whole modules.
- The twelve unused chat, vision, HTML and tool-loop helpers in the `api/` clients.
- `agents/text_to_bpmn/pipeline/retrieval/qdrant_index.py`, plus `all_chunks()` on the index interface and the in-memory index.
- `Pipeline.run_through_bpmn`, `StateMachine.is_terminal` / `_spec`, `EflintVerifierClient.submit` / `.status`, `LLMProvider._loads`, `config.redact` / `_SECRET_ENV_KEYS`, `Document.section_paths`, `ATTACHMENT_KINDS`, `VERIFY_WORDS` / `PETRI_NET_WORDS` (replaced by regexes), the unused `min_score` cache parameter, and the unused `api_key` provider parameters.
- Unused imports (ruff F401) across `agents/`, `api/` and `tests/`.

**Correction to the review:** it listed `eflint_verifier/agent.py::_strip_comments` as dead, but `infer_operation` uses it. It is kept.

The review also pointed to documents that don't exist. Those references (`todo/COORDINATOR_PLAN.md`, `todo/PROJECT_STATE_MANAGEMENT.md`, `src/…`) now point to `docs/planning-instructions/coordinator/IMPLEMENTATION_PLAN.md` / `PROJECT_STATE_MANAGEMENT.md` or the current module names. The `README.md` layout and the `agents/text_to_bpmn/README.md` package notes are updated to match.

---

## 5. Not done, and why

| Item | Status |
|---|---|
| **Default model upgrade** (R9 suggestion) | Not changed. The defaults are now defined in one place, but moving production from `claude-sonnet-4-6` / `gpt-4o` to a newer model changes cost and behaviour, so that is your call. It is a one-line change in `pipeline/config.py`, or set `ANTHROPIC_MODEL` / `OPENAI_MODEL`. |
| **P6:** stages re-read their inputs from disk | Kept on purpose: each stage can be resumed from stored artifacts, which the review noted is intentional. Only the resolver re-read was removed. |
| **B18 (second half):** re-uploading identical bytes under a new title keeps the old title | Unchanged. Content-hash idempotency is documented behaviour. Changing it means deciding whether titles are per upload or per document. |
| **PDF loader tests** (30 % coverage) and the OpenAI embedder (`embeddings.py`, 74 %) | No tests added. They need a PDF fixture and a mocked OpenAI client. They are the largest remaining coverage gaps, with `api/openai_client.py`'s network path at 52 %. |
| **Wheel-install test** (B20) | The packaging is fixed, but no CI step installs the wheel and calls a provider. |
| **Marking live tests** (`@pytest.mark.live`) | Not done. `test_cloud_qdrant.py` and `test_live_service.py` still run whenever their credentials or services are present. They also warn that qdrant-client 1.15.1 does not match server 1.20.0, which is a dependency-pin decision. |
| Remaining ruff style hints (SIM105/SIM102/RUF005 in demo/layout/render code) | Left alone. They don't change behaviour. |
| `QdrantSemanticCache` skeleton | Kept as the documented seam. It can no longer be selected until it is implemented (B15). |

---

## 6. New and changed files

- **New:**
  - `agents/contract/service.py` (shared agent REST app)
  - `agents/runs.py` (run folders and ids)
  - `tests/coordinator/test_review_fixes.py` (43 tests: classifier, ids, lifecycle, registry, service)
  - `tests/test_review_fixes_agents.py` (25 tests: shared service, `HttpAgent` errors, runs, vectorizer, library, upload limit, eFLINT bounds, verifier service)
- **Deleted:** `api/qdrant_remote_client.py`, `agents/text_to_bpmn/pipeline/content_provider.py`, `agents/text_to_bpmn/pipeline/retrieval/qdrant_index.py`.
- **Tests adjusted** to the fixed behaviour:
  - `test_coordinator_eflint.py`: B11, no service call.
  - `text_to_bpmn/test_llm_providers.py`: B2, a robust missing-SDK simulation.
  - `text_to_eflint/test_runs_browse.py`: P11, the lazy client.
  - `regulatory_docs/conftest.py`: B12, `delete_document` added to the fake store.
