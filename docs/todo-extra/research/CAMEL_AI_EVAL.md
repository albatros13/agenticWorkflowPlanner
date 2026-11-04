# Evaluation: Reimplementing the Agentic Workflow Planner with CAMEL-AI

**Question.** Could this framework be rebuilt on [CAMEL-AI](https://github.com/camel-ai/camel)
(`camel-ai` on PyPI, Apache-2.0)? Should it be?

**Short answer.** Yes, technically. Every component maps onto CAMEL somewhere. But CAMEL would
only replace the *plumbing*: LLM provider adapters, embeddings and Qdrant access, the chat-agent
loop, and possibly the transport. The value of this project is the deterministic, code-driven
parts: IR models, compilers, validators, the state machine, the PNML exporter, and the FLINT/SHACL
tooling. CAMEL has no equivalent for those, so they would be carried over unchanged. CAMEL's main
orchestration feature (`Workforce`, an LLM-driven task decomposition and assignment loop)
goes against the project's stated **"Code-Orchestrated Delegation"** principle. The coordinator
should therefore *not* become a CAMEL Workforce.

**Recommendation.** Don't do a full rewrite. A **selective adoption** is reasonable:
(1) add a `CamelProvider` adapter behind the existing `LLMProvider` interface to get CAMEL's broad
model catalogue, (2) optionally use CAMEL's `QdrantStorage`/embeddings in `regulatory_docs`, and
(3) optionally expose agents as CAMEL tools or MCP servers for interactive/exploratory use. Keep the
contract, registry, coordinator, and all compilers/validators as they are.

> Scope note: this evaluation is based on the repository as of 2026-10-29 (commit `a5fdc3a` plus
> uncommitted Petri-net work) and on CAMEL's public API (`ChatAgent`, `ModelFactory`,
> `FunctionTool`/toolkits, `Workforce`, `RolePlaying`, `camel.storages`, `camel.embeddings`,
> `camel.retrievers`, `camel.memories`). CAMEL changes quickly. Check exact class names and
> signatures against the version you would pin before acting on any of this.

---

## 1. What the current framework is

| Layer | Current implementation | Nature |
|---|---|---|
| Contract | `agents/contract/` (`AgentManifest`, `AgentRequest`, `AgentResult`, `AgentHandler`, `InProcessAgent`/`HttpAgent`) | Small typed REST+JSON contract, A2A-card-like manifest, transport-agnostic |
| Registry | `AgentRegistry.from_config(config/agents.json)` | Intent → agent routing, in-process or HTTP chosen by config |
| Coordinator | `agents/coordinator/coordinator.py` | Deterministic: classify intent → resolve project → **state-machine gate** → route → transition → persist |
| Intent | `HeuristicIntentClassifier` / `LLMIntentClassifier` | LLM does *classification + slot filling only*, structured JSON |
| Lifecycle | `StateMachine` from `config/project-states.json` | Data-driven FSM, refusal of forbidden actions |
| Persistence | `ProjectStore` (JSON), `ModelHandoff`, per-agent `runs/` | File-based, auditable |
| LLM access | `LLMProvider.generate_json(task, system, prompt, schema)` → `api/` (Anthropic/OpenAI) + `ScriptedLLMProvider` mock | Structured output only, single choke point |
| text→BPMN | 5-stage pipeline: ingestion → evidence → IR → terminology → compile/validate | Mostly deterministic; LLM used for extraction/interpretation |
| text→eFLINT | FLINT ontology + SHACL (rdflib/pyshacl), LLM SRL labelling | Deterministic validation + one LLM step |
| regulatory_docs | Content-hashed library, chunking, embeddings, Qdrant upsert with provenance | Deterministic pipeline |
| petri_net_generator | BPMN/IR/text → workflow net (PM4Py) → validation/soundness → PNML | Deterministic except the optional text → process-algebra IR call |
| Tests | ~174 tests, fully offline via `ScriptedLLMProvider`, hash embedder, in-memory Qdrant | Deterministic, no keys |

Roughly 11k lines of Python, of which only a small fraction is LLM plumbing.

---

## 2. Component-by-component mapping

| Current component | CAMEL counterpart | Fit | Notes |
|---|---|---|---|
| `LLMProvider` + `AnthropicProvider`/`OpenAIProvider` + `api/*_client.py` | `ModelFactory.create(model_platform=..., model_type=...)` + `ChatAgent.step(..., response_format=PydanticModel)` | **Good** | CAMEL supports many more providers (Anthropic, OpenAI, Gemini, Mistral, Ollama, vLLM, Azure, …). Its structured output uses Pydantic models, while the code here passes raw JSON Schema. You can bridge this with a dynamic Pydantic model, or by sending the schema as a tool definition. |
| `ScriptedLLMProvider` (offline mock) | CAMEL `ModelType.STUB` / custom stub backend | **Partial** | CAMEL's stub model returns fixed text. It does **not** route scripted responses by `task` the way the existing mock does. You would need a custom `BaseModelBackend` subclass to keep the offline test suite deterministic. |
| `LLMIntentClassifier` | `ChatAgent` with `response_format=IntentResult` | **Good** | Direct one-to-one replacement. No gain beyond provider breadth. |
| `HeuristicIntentClassifier` | — | n/a | Pure code; keep. |
| `Coordinator` control flow | `Workforce` (coordinator agent + task planner + workers) or `RolePlaying` | **Poor** | Workforce has an LLM decompose tasks and assign them to workers, with failure handling and replanning. This framework deliberately does **not** let an LLM decide routing or control flow. The state-machine gating (refusing forbidden actions) has no CAMEL equivalent and would have to stay in code anyway. |
| `StateMachine`, `ProjectStore`, `ModelHandoff` | — | n/a | No CAMEL equivalent; keep as-is. |
| `AgentHandler` / `AgentRegistry` (in-process) | `ChatAgent` with `FunctionTool`s, or a `Workforce` worker per agent | **Medium** | Each sub-agent can be wrapped as a `FunctionTool` (its `handle()` becomes the tool function). That only makes sense if an LLM is meant to call it. Here the coordinator calls agents directly. |
| `HttpAgent` + REST contract (`/agent/manifest`, `/agent/invoke_sync`) | CAMEL MCP support (`MCPToolkit` client; exposing agents/toolkits as MCP servers) | **Medium** | MCP is a viable *standard* replacement for the custom REST contract, and the manifest was designed to be A2A/agent-card-like anyway. But MCP is tool-oriented: async jobs (`Job`, `supports_async`) and the `artifacts_ref` convention would need to be re-expressed. |
| `regulatory_docs` embeddings (`openai`, `hash`) | `camel.embeddings.OpenAIEmbedding` (+ others: Sentence-Transformers, Mistral, Jina, …) | **Good** | Gains local/open embedding models for free. The `hash` test embedder would need a small custom `BaseEmbedding` subclass. |
| `regulatory_docs` `qdrant_store.py` | `camel.storages.QdrantStorage` (`VectorRecord`, `VectorDBQuery`) | **Good, with caveats** | Covers create/upsert/query and supports in-memory/local/remote Qdrant. Check that it preserves **stable `uuid5` point IDs** (overwrite instead of duplicate on re-vectorize) and the full provenance payload. The project pins `qdrant-client<1.16` for cloud-server compatibility, and CAMEL's own qdrant pin may conflict. |
| `regulatory_docs` chunking / `vectorizer.py` | `camel.retrievers.VectorRetriever` / `AutoRetriever` + `UnstructuredIO` loaders | **Partial** | CAMEL's retrievers chunk via Unstructured and store generic metadata. The current section-aware chunking with page/line-range provenance is *stricter*, and "Evidence over Memory / Auditability" depends on it. Keep the custom chunker. |
| text→BPMN ingestion (Markdown/PDF loaders, `SemanticChunker`) | `camel.loaders` (`UnstructuredIO`, etc.) | **Weak** | Possible but adds heavy dependencies and loses line-anchored provenance. |
| text→BPMN evidence extraction / logic interpretation (LLM stages) | `ChatAgent` + `response_format` | **Good** | Same as any structured LLM call. |
| IR models, BPMN/DMN compiler, auto-layout, XSD + graph validation | — | n/a | Domain-specific and deterministic; no CAMEL counterpart. |
| Terminology grounding (SNOMED/LOINC) | — (could be a custom toolkit) | n/a | Keep. |
| FLINT ontology / SHACL / SRL few-shot labelling | — | n/a | Keep; the LLM call could go through a `ChatAgent`. |
| Petri net generator (PM4Py, soundness, PNML) | — | n/a | Keep. |
| Semantic project cache seam (`cache/qdrant.py`) | `camel.storages.QdrantStorage` + embeddings, or `VectorDBMemory` | **Good** | A CAMEL-backed cache implementation could be a cheap way to fill this seam. |
| Chat UI (`static/index.html`) + FastAPI services | — | n/a | CAMEL doesn't provide a web service layer; FastAPI stays. |

---

## 3. What CAMEL would bring

1. **Provider breadth.** One `ModelFactory` covering dozens of platforms (hosted and local),
   instead of maintaining `api/anthropic_client.py` and `api/openai_client.py` by hand.
2. **Embeddings and vector stores.** Ready-made embedding classes and a `QdrantStorage`
   abstraction, plus other stores if Qdrant is ever replaced.
3. **Toolkits and MCP.** A large toolkit catalogue (search, browser, code execution, etc.) and MCP
   client/server support. This would matter if the planner grows towards open-ended, tool-using
   assistants, e.g. a research agent that gathers regulations from the web.
4. **Multi-agent patterns.** `RolePlaying` and `Workforce` suit *exploratory* tasks, for example
   brainstorming a process description with the user before it is committed and compiled, or
   critiquing a generated model.
5. **Memory and context management.** `ChatHistoryMemory` / `VectorDBMemory` / long-term memory for
   a conversational coordinator that remembers multi-turn context (the current coordinator is
   stateless per message apart from the project record).
6. **Community and research ecosystem.** Data generation, benchmarks, and agent-society research
   tooling, which may be relevant to a UvA research setting.

---

## 4. What CAMEL would cost or conflict with

1. **Philosophy mismatch on orchestration.** The README states: *"The LLM only classifies intent and
   extracts slots; Python decides control flow. Routing is never free prose."* CAMEL's
   distinctive orchestration (`Workforce`, `RolePlaying`) is LLM-driven planning and delegation.
   Using it for the coordinator would weaken auditability and determinism, which are the
   project's core guarantees. Using CAMEL *without* those features leaves mainly `ChatAgent` +
   `ModelFactory`, i.e. a model-access library.
2. **Structured-output contract.** Everything here goes through
   `generate_json(task, system, prompt, schema) → LLMResult` with provenance (`provider`, `model`,
   `task`, `raw_text`) for the audit log. CAMEL's `ChatAgent` is message/conversation-oriented and
   keyed on Pydantic `response_format`. The JSON-Schema prompts (`pipeline/schemas`, prompts) would
   need bridging, and the provenance record would have to be rebuilt from CAMEL's response objects.
3. **Offline determinism of tests.** The whole suite runs without keys through a mock that routes
   responses by `task`. Replicating this under CAMEL means writing a custom model backend. It
   works, but it's extra code rather than a saving.
4. **Dependency weight and pins.** `camel-ai` brings a large dependency tree (extras for each
   toolkit/storage). The repo deliberately keeps per-agent extras small (`text_to_bpmn`,
   `petri_net`, …) so each agent can run alone. Watch for conflicts with the `qdrant-client<1.16`
   pin, `pydantic>=2.13`, and PM4Py (AGPL-3.0, already isolated in its own extra).
5. **API churn.** CAMEL is under active development, and module paths and signatures (especially
   Workforce, MCP, structured output) have changed between minor versions. A thin adapter limits
   the blast radius. A deep rewrite does not.
6. **Transport and async jobs.** The current contract has explicit async `Job` semantics and
   `artifacts_ref` hand-off. CAMEL/MCP don't model "a long-running job producing artifacts on disk
   referenced by run ID" directly; that convention would have to be layered on top.
7. **Little reduction in code.** Most of the ~11k LOC is domain logic: compilers, validators, IR,
   ontology, Petri-net analysis. A full CAMEL port would remove only the `api/` clients, the provider
   adapters, and parts of `regulatory_docs`, perhaps 5–10% of the code. Migration and retesting
   would cost more than that.

---

## 5. Options

| Option | Effort | Risk | Benefit | Verdict |
|---|---|---|---|---|
| **A. Full rewrite on CAMEL** (Workforce coordinator, ChatAgent sub-agents, CAMEL retrievers) | High | High: breaks determinism/audit guarantees and offline tests | Ecosystem alignment | **Not recommended** |
| **B. CAMEL as a model backend**: add `CamelProvider(LLMProvider)` wrapping `ChatAgent`/`ModelFactory`, selected by config in `llm/factory.py` | Low | Low: behind an existing seam; mock unchanged | Access to many more models (incl. local) | **Recommended** |
| **C. CAMEL storage/embeddings in `regulatory_docs`** and for the semantic project cache | Low–Medium | Medium: verify ID stability, payload provenance, qdrant pin | Less custom vector code, more embedding choices | **Optional**; do it when the cache seam is implemented |
| **D. Expose agents as CAMEL tools / MCP servers** (alongside the REST contract, not replacing it) | Medium | Low if additive | Interop with CAMEL/MCP clients; lets a CAMEL `ChatAgent` drive the planner interactively | **Optional**, useful for demos or external integration |
| **E. CAMEL multi-agent patterns for *pre-compilation* tasks only** (e.g. a RolePlaying "process analyst ↔ domain expert" session that drafts or critiques a description before `engineer_process`) | Medium | Low: output still flows through the deterministic pipeline | Better input descriptions; research value | **Optional / exploratory** |

### Sketch of option B

The adapter fits behind the existing interface without touching agent or pipeline code
(illustrative only, verify against the pinned CAMEL version):

```python
# agents/text_to_bpmn/pipeline/llm/camel_provider.py  (illustrative, not implemented)
class CamelProvider(LLMProvider):
    name = "camel"

    def __init__(self, platform: str, model: str):
        from camel.models import ModelFactory          # lazy, optional extra
        from camel.types import ModelPlatformType
        self._model = ModelFactory.create(
            model_platform=ModelPlatformType(platform), model_type=model)
        self._model_name = model

    def generate_json(self, *, task, system, prompt, schema, max_tokens=4096) -> LLMResult:
        from camel.agents import ChatAgent
        agent = ChatAgent(system_message=system, model=self._model)
        # Bridge JSON Schema -> Pydantic (e.g. a generated model) or pass the schema in the
        # prompt/tool definition; validate the result with self._loads() as other providers do.
        resp = agent.step(prompt, response_format=pydantic_model_from_schema(schema))
        raw = resp.msgs[0].content
        return LLMResult(data=self._loads(raw), raw_text=raw, provider=self.name,
                         model=self._model_name, task=task)
```

This would ship as a new `camel` extra in `pyproject.toml`, so the core package and the offline
test suite remain CAMEL-free.

---

## 6. Conclusion

- **Feasible:** yes. No part of the system *needs* anything CAMEL lacks, and everything
  CAMEL lacks (compilers, validators, FSM, ontology, Petri-net analysis) is plain Python that can
  be carried over.
- **Worth a full reimplementation:** no. CAMEL's strengths (LLM-driven multi-agent
  collaboration, open-ended tool use) are the opposite of this project's design goals
  (deterministic compilation, code-orchestrated delegation, evidence-anchored auditability). A
  rewrite would replace a small amount of plumbing at high cost and risk to those guarantees.
- **Worth partial adoption:** yes, through the seams that already exist. Add a `CamelProvider`
  (option B) for model breadth, consider CAMEL vector storage for the semantic cache or regulatory
  docs (C), and keep MCP/agent-society features as additive, non-authoritative layers (D, E).
