# Generating Tests for GenAI Agents: A Survey of Practice

**Focus: test data handling, and invariants over results and trajectories**
**Written to inform Task 4.3 (automated ETL pipeline for agentic AI quality assessment)**

---

## 1. Scope and reading guide

This survey covers how practitioners and researchers currently generate, curate, and version the data used to test GenAI-enabled agents, and what they assert about the results. It is organised around two questions that matter for Task 4.3:

1. **Where do test inputs come from, and how are they handled** so that a quality assessment is reproducible and auditable rather than a snapshot of whatever the model happened to do that afternoon?
2. **What can be asserted** about an agent run when there is no ground-truth answer, and how are those assertions encoded so that they survive model upgrades, prompt changes, and topology changes?

Section 6 addresses the specific question of whether the differential-testing techniques from the partner paper on the CRN medical rule engine transfer to general-purpose ETL. The short answer is that they transfer well, and in some respects transfer better to ETL than to the original setting, but the notion of "mismatch" has to be redefined from label equality to relational diff. Section 9 collects the design implications as a set of concrete recommendations.

Two anchor sources frame the discussion. The Mitesh Shah post [1] is a good representative of consolidated industrial practice as of mid-2026. The partner paper, LLMeDiff [2], is the strongest available demonstration that LLM-generated tests plus differential execution can find real faults in a regulated data-validation system.

---

## 2. Why agent testing needs different machinery

Three properties break the assumption that traditional testing rests on, namely that the same input yields the same output.

**Non-determinism does not disappear at temperature zero.** Practitioner reports and vendor documentation converge on this: reduced sampling temperature narrows the output distribution but does not eliminate variation, and multi-step agents compound whatever variation remains [1, 17]. A property that holds on one run is not a property. This has a direct methodological consequence: every assertion needs a repetition policy attached to it, and every reported score needs a variance estimate. LLMeDiff handles this correctly by repeating each generation 30 times and applying non-parametric tests with effect sizes [2], following established guidance for randomised algorithms in software engineering [19].

**Side effects are the failure surface.** An agent that produces fluent prose but calls the wrong tool with the wrong arguments has done real damage. The consensus in practice is that tool selection and argument correctness deserve stricter, cheaper, more deterministic checks than final-output quality [1, 11, 15].

**There is usually no oracle.** This is the same oracle problem catalogued by Barr et al. [8], and the same two classical answers apply: differential testing, which runs the same input through two implementations that should agree [9], and metamorphic testing, which checks relations between related executions rather than absolute correctness [5, 6]. Both are now heavily used for LLM-based systems; a recent systematic review of 93 studies characterises the relationship as bidirectional, with metamorphic testing used to verify LLMs and LLMs used to discover metamorphic relations and synthesise follow-up inputs [7].

### 2.1 The cost-tiered structure everyone converges on

Industrial practice has settled on a three-tier structure, with slightly different names across sources but the same shape [1, 15]:

| Tier | What it checks | Cost | Cadence |
|---|---|---|---|
| Deterministic assertions | Schema validity, enum membership, tool names, argument types, budget limits, negative safety patterns | Milliseconds, free | Every commit, gating |
| Model-based evaluation | Groundedness, completeness, task achievement, tone, policy compliance | Seconds, metered | Nightly, pre-release, after prompt or model change |
| Live experiments | Preference between versions on real traffic | Days, risk-bearing | After the first two are clean |

The operational advice attached to this is worth restating because it is frequently ignored: do not spend a model call on something a string comparison can decide [1]. The corollary for Task 4.3 is that the pipeline should make the cheap tier as expressive as possible, which is largely a matter of how well the test data is structured and annotated.

Two further practices belong here. First, **response caching** for the model-based tier, keyed on model, endpoint, prompt, and context, with an expiry so that periodic fresh evaluation still happens [1]. Second, a deliberate split between **reference-based evaluation** in CI, where a golden dataset supplies expected behaviour and the question is regression, and **reference-free evaluation** in production, where no golden answer exists and the question is absolute quality on sampled live traffic [1].

---

## 3. Test data: provenance, generation, and hygiene

This is the section most directly relevant to Task 4.3, since the pipeline is meant to supply exactly this material.

### 3.1 Four sources, used in combination

**Hand-authored golden cases.** The consistent recommendation is to start with 20 to 50 hand-written cases covering happy paths, edge cases, adversarial inputs, and failure scenarios, and to treat the act of writing them as a specification exercise [1]. Teams that skip this step discover that they cannot articulate what "correct" means, which is a design problem rather than a testing problem. The complementary artefact is an explicit list of prohibited behaviours, which becomes the negative test suite.

**Mined production traces.** Once an agent is live, the highest-value source of new cases is real traffic, and specifically real failures. The loop is: observe a failure, add the input to the dataset with the expected correct behaviour, verify the fix, keep the case forever [1, 11]. Langfuse describes the same loop with an additional step that is important for this survey: when a bad trajectory is found, do not only add the input, also write down **the property that was violated** ("must not call the payment tool twice") as an executable code evaluator that runs in every future experiment [11]. That turns an incident into an invariant rather than into a single example.

**Synthetic generation.** Used for coverage rather than authority. The dominant architecture in 2026 is seed-and-evolve: start from real seeds, then apply evolution operators such as rephrasing, complexity escalation, constraint addition, and persona variation [56-family sources; summarised in 20]. Persona-driven simulation has become the standard way to generate multi-turn agent test data, with a simulated user pursuing a scenario while the agent works [3, 4, 20]. Recent work makes the simulated user an explicit, tunable, evolved component rather than a hidden default, on the finding that small changes to simulated user behaviour (impatience, low cooperativeness) move headline success rates substantially [21].

Three practical constraints on synthetic data recur across sources:

- Use a generator model from a different family than the system under test, to limit self-preference and shared blind spots.
- Run contamination checks: rephrase before scoring, and compare scores across multiple test-set versions to detect memorisation.
- Mix synthetic with curated production traces rather than going pure synthetic, since synthetic data covers the imagined distribution and production traces cover the real one.

**Recorded fixtures.** Distinct from the above because they are not inputs but captured environment behaviour. See 3.3.

### 3.2 The dominant failure mode: volume without signal

Every practical source warns about the same thing. A large dataset full of near-duplicates inflates the pass rate while adding no discriminative power [1]. The countermeasures are mechanical and should be pipeline features, not manual discipline:

- **Embedding-based deduplication** with an explicit similarity threshold. Published agent-environment generation pipelines use cosine similarity thresholds around 0.85 with category caps to prevent over-representation of dominant scenario types [22].
- **Stratification metadata on every case**: difficulty, capability category, source (hand-written, mined, synthetic), whether it came from a production incident, and which failure mode it targets. This is what makes it possible to slice results and say where the agent is weak, rather than reporting a single number.
- **Coverage accounting against a declared taxonomy** rather than against dataset size.

### 3.3 Record and replay as the determinism boundary

The most consistently reported technique for making agent tests runnable in CI is record-and-replay of the non-deterministic dependencies [15, 16, 17]. The pattern:

- A test provider wraps the real model provider. In **record mode** it calls the real model and serialises request and response, keyed by a hash of the input messages. In **replay mode** it returns the recorded response without calling anything.
- The same wrapper is applied to tools. Some implementations replay tool responses too; others deliberately let tools execute for real while replaying only model decisions, on the grounds that the model call is the expensive non-deterministic part and the tool execution is the part whose real behaviour you want to test [16].
- The recorded trace is append-only and streamable, with a monotonically incrementing step identifier and a run identifier. JSONL is the common choice. What gets recorded: full prompt, all sampling parameters, model identifier and version, response tokens, tool name and arguments and complete response including errors and timeouts, timestamps, random seeds, and the inputs and outputs of any router, planner, or classifier as distinct events [17].
- Fixtures are treated as code: reviewed on diff, credentials redacted, with a note recording why a fixture changed (provider SDK upgrade, endpoint version change, schema drift, intentional coverage expansion).

The value for Task 4.3 is that replay separates variables. Holding recorded model responses fixed while varying orchestration topology isolates the effect of the topology. Holding the topology fixed while varying the data version isolates the effect of data quality. Without a replay layer, the testbed cannot attribute a performance difference to any particular cause.

The limitation is equally clear and is stated bluntly in practitioner accounts: replay validates the deterministic layers, and live model evaluation is deliberately kept out of the per-commit path because it is expensive, slow, and flaky [15]. Replay tells you whether the agent still works on known inputs. It does not tell you whether it works on new ones.

### 3.4 Environment state as test data

The strongest oracle available for agents comes from making the environment itself part of the test fixture. τ-bench is the canonical example: the agent converses with a simulated user, calls domain APIs, and the grade comes from **comparing the database state at the end of the conversation against an annotated goal state** [3]. The transcript is ignored. Either the refund was issued or it was not.

Two design decisions there are worth borrowing directly:

- **Policy guidelines as a separate artefact from the task.** This creates a failure category that pure task benchmarks cannot express, namely succeeding at what the user asked in a way the organisation does not permit [3, 4].
- **State-based grading with an annotated goal state.** This converts an open-ended generation problem into a state-equality problem, which is checkable without a judge model.

The infrastructure cost is a high-fidelity mock environment. Published examples build standalone REST services backed by SQLite that replicate production API surfaces including error codes, and validate fidelity by capturing golden request-response pairs from real accounts and checking mock responses against them, including mutation side effects [23]. This is a substantial engineering artefact, and it is the same artefact that a Task 4.3 testbed needs.

### 3.5 Versioning, lineage, and reproducibility

The tooling landscape here is mature and the choice is architectural rather than about capability [13]:

| Tool | Granularity | Fit |
|---|---|---|
| DVC | Project-level, file-based, Git-centric | Content-hashed pointers committed to Git, actual data in a cache or remote; adds pipeline definition and dependency-driven re-execution, so changing input data re-runs validation, training, and evaluation while changing only a hyperparameter re-runs less |
| lakeFS | Repository-level over object storage | Git-like branching and commits exposed through an S3-compatible API, so existing engines read versioned data unchanged; suits isolated experiment branches without duplicating terabytes |
| Iceberg / Delta Lake / Hudi | Table-level | Transaction log plus time travel; a run can reference an exact table snapshot identifier forever |
| Dolt | Row-level in SQL | Git semantics inside a database |

The relevant caution for a research project: DVC improves reusability and provenance within an active project but does not by itself deliver findability or accessibility under FAIR, which still require deposition with a persistent identifier and clear licence terms [13]. For versioned data releases intended to support reproducibility across partners, the pipeline needs both an internal versioning mechanism and an external publication step.

A practical convention that recurs: every evaluation run should emit a **manifest** that names the dataset version, the model and version, prompt versions, tool schema versions, environment snapshot, sampling parameters, and the code commit. Trajectory evaluation guidance makes the same point from the other direction, recommending that the business context behind each reference trajectory be versioned, so that when a schema, tool, or policy changes, the affected trajectories are re-evaluated while prior versions remain available to keep earlier scores explainable and auditable [12].

---

## 4. Invariants: what can actually be asserted

This is the second core question. The following taxonomy is synthesised from the sources; the layering is mine, but each layer is attested in practice.

### L0. Structural invariants over the output

Cheapest and most productive per unit of effort. Output parses as the declared schema; enumerated fields take permitted values; length and token budgets respected; no PII pattern present; classification falls in the expected label set [1]. A large fraction of production incidents reduce to malformed structured output or a wrong function name, and none of these need a judge.

### L1. Step-level invariants over tool calls

- **Tool selection**: the correct tool was chosen for the request. Equally important, the negative form: an informational query must not trigger a mutating tool [1].
- **Argument correctness**: types, units, dates, identifiers. The failure modes here are ordinary and severe, and the source is emphatic that near-enough is not acceptable for arguments, since a date off by one is a real-world error rather than a semantic variation [1].
- **No fabricated arguments**: every argument value traces to something present in the user request or a prior tool result. This is checkable mechanically and is one of the most useful assertions available.
- **Confirmation before destructive action**.

### L2. Trajectory-level invariants

The step-level checks generalise into properties over the whole action sequence:

- **Ordering**: a search must precede a booking. Expressed as index comparison over the tool call log [1].
- **Absence**: a named tool must never appear; a tool must never appear twice [11].
- **Budget**: step count, token cost, and wall-clock time bounded. A trajectory budget evaluator is a standard code evaluator [11].
- **Non-looping and progress**: no repeated identical call without an intervening state change.
- **Error recovery as first-class behaviour**: on tool failure or empty result, the agent must report, retry within an allowed fallback, or ask for clarification, and must not synthesise a successful result [1].
- **Plan adherence**: whether the agent formed a sensible plan and then followed it as tool outputs arrived. Trajectory-aware protocols additionally define a step-wise validity indicator that flags steps exhibiting deviation, precisely to catch the case where a correct final answer is reached by a deviated path [10, 24].

Aggregated trajectory views, which collapse repeated steps into nodes with counters and reveal cycles at a glance, are reported as the fast way to spot pathological trajectories during error analysis, with expanded per-call views used to pin down a specific run [11].

### L3. End-state invariants

Final world state matches an annotated goal state [3]. Where a sandbox exists this dominates all textual evaluation and should be preferred. The generalisation of this idea to metamorphic testing is the **action metamorphic relation**, where two differently-phrased task descriptions that should produce the same final system state form a metamorphic pair regardless of intermediate actions or response wording [24]. This is a strong, cheap, label-free oracle and it is under-used.

### L4. Cross-run invariants

An invariant that holds once is anecdote. τ-bench introduced pass^k, requiring success in all k attempts, alongside the usual pass@k which requires success in at least one [3]. The arithmetic is unforgiving and worth stating explicitly in any report: an agent succeeding 75 percent of the time per trial has pass@3 of about 98 percent and pass^3 of about 42 percent [12]. For any agent whose output is acted upon rather than reviewed, pass^k is the honest metric. Flaky assertions should be investigated rather than retried, since flakiness usually indicates an over-strict assertion or an ambiguous prompt [1].

### L5. Cross-input invariants (metamorphic)

No oracle needed. Candidates that appear across the literature and that apply to agents:

- Paraphrase invariance of the resulting action.
- Irrelevant-context invariance: adding unrelated content to the prompt must not change the action.
- Order invariance over retrieved documents or tool result lists.
- Unit, locale, date-format, and encoding invariance.
- Monotonicity: adding a constraint may only narrow the result set.

Metamorphic relation discovery is itself being automated with LLMs, with relations extracted from specifications or rules into structured patterns (Gherkin-style given-when-then is a recurring representation because it is both machine-processable and human-inspectable) and then compiled into executable follow-up tests [7, 25]. The consistent caveat is that LLM-proposed relations are not reliable by default and need guardrails and human review before admission to a suite [25].

### L6. Cross-implementation invariants (differential)

Two implementations that should agree, run on the same input, with disagreement flagged. This is the LLMeDiff pattern [2] and is treated separately in Section 6.

For an agent testbed the differential axes are: model version, prompt version, orchestration topology, and framework. Since Task 4.3 includes a testbed for experimentation on topologies and orchestration patterns, differential execution across topologies with a fixed task set and fixed replayed model responses is the natural experimental design.

### L7. Negative and adversarial invariants

Direct prompt injection resistance, indirect injection through retrieved documents and tool outputs, system prompt non-disclosure, refusal boundaries, and absence of ungrounded content. The OWASP Top 10 for LLM Applications is the standard reference for the risk taxonomy, with prompt injection, insecure output handling, excessive agency, and sensitive information disclosure the most relevant entries for agents [1]. Manual red teaming by someone who did not write the prompts is reported as the single highest-return activity in the whole testing programme; automated tooling such as PyRIT covers breadth with multi-turn escalation strategies [1]. The scheduling advice is that red-team scans belong to every change in prompts, tools, retrieval content, or model, rather than to a launch checklist.

### L8. Data-provenance invariants

This layer is not prominent in the industrial sources and is where Task 4.3 has something to contribute. Proposed properties, each checkable given a well-instrumented pipeline:

- Every factual claim in the agent output traces to a retrieved record with a known dataset version.
- The agent never reads a record whose validation status is quarantined.
- Tool inputs conform to the schema of the dataset version supplied to the run.
- Harmonised codes appearing in agent output belong to the target vocabulary version declared in the manifest.
- The set of source records touched by a run is recoverable from the trace.

The reason this matters is attribution. Task 4.3 is meant to support detection, diagnosis, and remediation of **data-related** faults. Distinguishing a data fault from a model fault requires holding one fixed while varying the other, and requires the run record to carry enough provenance to make the attribution checkable rather than inferred.

### 4.1 How invariants are encoded

Three mechanisms, used together:

**Code evaluators.** Plain assertions over the structured trace. Free to compute, so they can run on every sampled production trace as well as in CI. A boolean tool-call score computed on live traffic doubles as an alerting signal [11].

**Schema and expectation suites.** For the data side, see Section 7.

**Judge models with structured rubrics.** For the genuinely open-ended dimensions. The difference between a useful judge and an expensive random number generator is the rubric, and the most cited design element is the **hard-fail cap**: certain failures cap the score regardless of other criteria, so that a fabricated booking reference cannot be averaged away by good tone [1]. Two further recommendations: run separate evaluators per output component rather than a single aggregate, so that a quality drop identifies which dimension degraded; and report changes against a baseline rather than absolute scores, since "relevance dropped 8 percent since last release" is actionable and "relevance is 0.87" is not [1].

Known judge biases to control for and to document: verbosity bias, position bias in pairwise comparison, and self-preference toward the judge's own model family [1].

---

## 5. Continuous integration and cadence

The consolidated recommendation [1, 15]:

- Deterministic assertions gate every merge and complete in under a minute.
- Judge-based evaluation runs nightly, before releases, and after any prompt or model change, with response caching.
- Red-team scans run weekly or before major releases and after any change to prompts, tools, retrieval content, or model.
- Replay regression suites gate promotion; canary on a small traffic slice with online judging comes next; A/B only after both are clean, with aggregation over many sessions and a consistent seed policy across arms [15].

---

## 6. Differential testing of rule engines, and its transfer to ETL

### 6.1 What LLMeDiff does

The partner paper addresses testing GURI, the rule validation subsystem of the Cancer Registry of Norway's registration support system, which validates incoming cancer messages against medical rules authored by medical coders from ICD-10, ICD-O-2, and ICD-O-3 [2]. The four testing challenges the authors identify are evolution, cost, labour-intensity, and absence of test oracles. The approach has two stages.

**Stage 1, test generation.** One prompt per rule produces exactly three tests: a satisfying case, a violating case, and an invalid case, corresponding to GURI's three-valued result of Pass, Fail, and NotApplied. The prompt is a numbered procedure that instructs the model to restate the rule in plain language, identify variables and generate values, alter only the implied value for the violating case while keeping others unchanged, avoid repetition, emit a confidence score, and format the whole as a JSON object. Notably, the prompt deliberately uses propositional-logic vocabulary rather than GURI-specific terminology, on the reasoning that the model understands the former better.

**Stage 2, differential execution.** The same tests run against GURI and against Dvare++, a simplified reference implementation wrapping the same underlying rule engine so that it returns results in GURI's categorisation. A preprocessing step embeds each test, which contains only the variables of its own rule, into a complete cancer message using a fixed set of values for the other variables. Mismatches are reported for developer investigation.

**Measurement.** Effectiveness is a completion rate based on exact match against the expected output structure, treating instruction inconsistency as a hallucination indicator, plus a success index combining how often a valid output was produced with how often it was semantically correct. Robustness is measured by applying eight mutation operators to the rules (altering comparison operators, altering dates, changing and/or, negating inequality, reversing set inclusion, swapping startswith and endswith, swapping antecedent and consequent, swapping substring indices), producing 322 mutants from 58 rules, and measuring how much the success index moves. Everything is repeated 30 times with Kruskal-Wallis, Dunn post-hoc, Benjamini-Yekutieli correction, and Vargha-Delaney effect sizes [2].

**Findings that matter methodologically**, independent of which models were best at the time:

- Effectiveness and fault-finding are not the same thing. The most effective generator did not find the most mismatches, and no model subsumed another: each detected a different subset of the inconsistency types, and none found all 22 [2].
- Generating negative cases is harder than positive ones, because the generator must satisfy the antecedent while falsifying the consequent [2].
- Mismatches are not faults. The authors are explicit that the value lies in actionable insight, that maximising mismatch count is not the objective, and that root cause still needs a human [2].
- LLMs miss trivial inputs. Because the model interprets variable names, it always emits a well-formed date string and never tries a bare integer where a date is expected. A conventional fuzzer would try that immediately, and in this case it would have found a real inconsistency [2].
- The reference implementation is a maintained artefact. The authors suggest adapting it over time to absorb edge cases that previously produced false positives, and raise the possibility of a learned reference implementation [2].

The inconsistencies actually found were in handling rule versions, date formats, and variable dependencies. All three are data-representation faults rather than logic faults, which is exactly the class Task 4.3 is concerned with.

### 6.2 What transfers to general-purpose ETL, and how

The mapping is close, and in two respects ETL is a friendlier setting than the original.

**The declarative specification exists.** LLMeDiff needs a rule to generate from. ETL pipelines have equivalents: JSON Schema, Avro, and SHACL shapes; source-to-target mapping specifications; harmonisation rules including unit conversions and code-system crosswalks; data contracts; dbt model definitions and their test declarations; constraint sets. Each of these can play the role of the medical rule.

**The three-valued outcome maps cleanly.** Pass, Fail, and NotApplied correspond to *loaded*, *quarantined*, and *filtered or not-applicable* at the record level. So the per-rule triple generalises directly: for each validation or mapping rule, generate one record that should be accepted, one that should be rejected by that specific rule, and one to which the rule does not apply. This gives per-rule coverage of the validation layer by construction, which is more than most expectation suites achieve.

**Reference implementations are easier to obtain than in the GURI case.** The paper had to build Dvare++ because alternative implementations of a highly customised system are unlikely to exist. ETL transformations are usually expressible twice, cheaply, which opens several differential axes:

| Axis | Reference side | Faults it catches |
|---|---|---|
| Engine | Same logic in DuckDB or pandas versus production Spark or distributed execution | Engine-specific null, collation, type-coercion, and floating-point semantics |
| Implementation | Naive single-threaded reference versus optimised production path | Optimisation bugs, incorrect pushdown, wrong join strategy |
| Incrementality | Incremental load versus full recompute over the same window | The single most common class of ETL fault: late-arriving data, missed updates, non-idempotent merges |
| Mode | Batch versus streaming path for the same semantics | Windowing and watermark errors |
| Version | Pipeline vN versus vN-1 on a pinned input snapshot | Unintended behaviour change; this is regression testing with a data-diff oracle |
| Parallelism | Different partition counts or worker counts | Cross-row state leakage, order dependence |

**Rule mutation transfers, and gains a second use.** In LLMeDiff, mutating the rules measures the robustness of the *generator*. That use carries over directly to mapping rules. But the same operators applied to the *transformation code* give something the data-quality field largely lacks: a mutation score for a validation suite. Deliberately break the transformation (swap join keys, drop a filter predicate, change a rounding mode, flip a null-handling branch, alter a date offset, reverse an inclusion test) and measure what fraction of mutants the expectation suite catches. This answers "is my Great Expectations suite any good", which is otherwise unanswerable, and it is a natural experimental contribution.

**A third mutation target: the data itself.** Inject known defects into a validated dataset (schema drift, unit errors, stale records, duplicates, label noise, systematic missingness, encoding corruption, vocabulary version skew) and measure the resulting degradation in agent behaviour. This produces a **data-sensitivity profile** per agent and per topology, and it is the mechanism by which the pipeline can support diagnosis rather than only detection. It is the exact mirror of LLMeDiff's rule mutation, moved one level down.

### 6.3 Metamorphic relations for ETL

Differential testing needs a second implementation. Metamorphic testing needs nothing, which makes it the cheaper first move. Concrete relations for a harmonisation pipeline, each mapping to a real fault class:

| Relation | Statement | Catches |
|---|---|---|
| Permutation | Row order in the source does not change the loaded result, modulo declared ordering | Order-dependent aggregation, non-deterministic dedup tie-breaks |
| Partition additivity | T(A ∪ B) equals T(A) ∪ T(B) for row-wise transforms | Accidental cross-row dependence, state leakage between records |
| Idempotence | T(T(x)) equals T(x) for normalisation and harmonisation steps | Repeated unit conversion, double-applied scaling, cumulative trimming |
| Replay determinism | Re-running from a checkpoint yields identical output, modulo declared volatile columns | Hash ordering, dictionary iteration, uncontrolled parallelism, wall-clock leakage |
| Subset consistency | Restricting the source to a subset yields exactly the corresponding subset of the target, for row-wise steps | Hidden global state, incorrect windowing |
| Representation invariance | The same measurement expressed in different units, date formats, or encodings maps to the same canonical value | Exactly the date-format fault LLMeDiff found in GURI |
| Additive schema | Adding an unused source column does not change the output | Positional rather than named column access, brittle projections |
| Duplicate behaviour | Exact duplicates either collapse or multiply linearly, never something in between | Partial dedup, non-deterministic keys |
| Null injection | Adding a nullable optional field does not change existing derived fields | Coercion cascades, null-propagation errors |
| Aggregation reconciliation | Sums over partitions equal the total; counts before and after a join reconcile against the declared cardinality | Join fan-out, silent row loss |
| Identifier renaming | Consistently renaming an opaque key does not change output shape | Accidental semantics attached to identifier values |

These are cheap, label-free, and run on any dataset including production data. For a pipeline handling heterogeneous sources they are likely to be the highest-yield checks available.

### 6.4 What does not transfer without redesign

**Mismatch is a diff, not an equality.** GURI returns one of a small set of labels, so a mismatch is a scalar disagreement. An ETL stage returns a relation, so a mismatch is a set difference over possibly millions of rows. This requires:

- Key-based row matching with an explicit key declaration, rather than positional comparison.
- A **tolerance policy** as a first-class, versioned artefact: numeric tolerances per column, an ignore-list for legitimately volatile columns (ingest timestamps, surrogate identifiers, ordering columns), and declared normalisations applied before comparison.
- A **mismatch summariser** that clusters row-level differences into a small number of patterns with representative examples and affected-row counts. Dumping raw row diffs into a triage queue makes the queue useless. LLMeDiff's own manual analysis grouped 44 raw mismatches into a handful of causes; that grouping step needs to be automated here because the volumes are larger by orders of magnitude.

**Generation must be hybrid.** The paper's own finding is decisive: an LLM will not produce the degenerate input that a fuzzer produces immediately. The recommendation is to combine LLM generation, for semantically valid and domain-plausible records that respect cross-field dependencies such as a diagnosis date preceding a treatment date, with schema-driven property-based generation (Hypothesis, schemathesis, or equivalent) for boundary values, type violations, encoding edge cases, and structural degeneracy. Neither alone is adequate.

**The generator is a component that regresses.** LLMeDiff's completion rate is effectively a hallucination metric for the test-generation stage. In a production pipeline this should be a monitored gate: if the fraction of generator outputs failing schema validation rises after a model upgrade, the test corpus is silently degrading. Track it, version the generator model and prompt in the dataset manifest, and re-generate rather than accumulate.

**The reference implementation needs governance.** It is a maintained artefact with its own version, its own test suite, and a changelog of deliberately accepted deviations from production. Without that, absorbed false positives become unrecorded specification changes.

---

## 7. Data validation as the substrate

The agent-side and data-side practices meet here. The mature open-source options and their positioning [14]:

- **Pandera** for schema-based validation of dataframes at the boundaries of analytical code. Lightweight, in-process, closest to the transformation.
- **Great Expectations** for portable expectation suites across engines, with generated documentation that functions as a shared contract between producers and consumers. Expectation suites are stored in version control alongside pipeline code.
- **Soda** for orchestrated checks integrated with Airflow, Dagster, Prefect, and dbt, with CI integration.
- **dbt tests**, with the important structural caveat that they run after the model materialises, so they catch a broken state rather than preventing it from being written [14].
- **Pydantic** and similar for guarding process boundaries and deserialisation.

Three placement decisions matter more than tool choice:

**Gate position.** Validation at the ingest boundary rejects bad rows before they are written; validation after materialisation only detects. For a pipeline whose purpose is to guarantee trustworthy inputs to quality assessment, the gate belongs at ingest, with rejected rows routed to a quarantine store rather than discarded.

**Quarantine rather than halt, by default.** Halting the DAG is correct for schema-breaking failures and wrong for a small fraction of malformed records. The distinction should be declared per expectation, with severity levels, and the quarantine store is itself a versioned dataset and a source of human-in-the-loop review cases.

**Sampling policy for high-throughput paths.** Validating every record is not always affordable. Documented approaches are validating every Nth record, batching over time windows, and increasing validation frequency adaptively when anomalies are detected [14]. Whichever is chosen, the sampling rate belongs in the manifest, because it bounds what the validation result actually claims.

---

## 8. Human-in-the-loop, and generating labelled sets for RLHF

Two distinct uses of human effort, often conflated.

**Adjudication.** Ambiguous or critical cases routed to an expert for a decision. The pipeline requirements are a priority ordering (novelty of the mismatch signature, number of affected rows, confidence of the automated classifier), a stable case identifier, and capture of the rationale rather than only the verdict. Every adjudication is simultaneously a labelled example and a candidate new invariant. That second output is the more valuable one and is easy to lose: the Langfuse practice of writing down the violated property alongside the case [11] should be a required field in the adjudication form, not an optional note.

**Preference data.** The literature on preference dataset quality is consistent on a few points that constrain pipeline design:

- Pairwise comparison is preferred to absolute scoring because humans judge relative quality more consistently [26].
- Noise in preference annotations is common and materially degrades alignment, with reported noise levels in real datasets exceeding 20 percent in some analyses [27]. Inter-annotator agreement is therefore a measured, reported quantity, not an assumption, and calibration rounds on shared examples with expert adjudication of contested items are standard practice [26, 28].
- The most common task-design failure is producing near-tie comparisons at scale. When annotators consistently judge both responses acceptable, the reward model learns noise [28].
- Prompt distribution matters as much as annotation quality. Over-representing easy categories such as factual question answering, and under-representing multi-step reasoning, appropriate refusals, and structured output, produces a model that is uneven across the real task distribution. Building a representative distribution requires deliberate design rather than opportunistic curation [28].
- Model-assisted annotation, where an early reward model pre-ranks and humans review only borderline cases, is reported to reduce annotation volume substantially for the same effective signal [28]. This is an active-learning loop and should be a pipeline feature.

For an agent setting the unit of preference is a trajectory rather than a response, which raises a design question the pipeline has to answer explicitly: are annotators comparing final outputs, full trajectories, or specific decision points? Comparing at the decision point gives denser and more attributable signal but requires the trace to be segmented, which is another argument for structured trajectory capture.

---

## 9. Design implications for Task 4.3

Consolidating the above into recommendations.

**On the pipeline as a producer of test data.**

1. Treat the evaluation corpus as a first-class versioned data product with the same release discipline as the harmonised data: immutable versions, a manifest, and a changelog.
2. Attach stratification metadata to every case at creation time. Retrofitting it is not feasible.
3. Make deduplication and coverage accounting pipeline stages, not review checklists.
4. Emit quarantined records as a labelled negative corpus. A record that failed validation for a known reason is a free, correctly-labelled test case for the layer downstream.
5. Version the test generator alongside the tests, and monitor its schema-conformance rate as a regression signal.

**On invariants.**

6. Prefer state-based oracles wherever a sandbox exists. They are stronger and cheaper than any judge.
7. Adopt metamorphic relations before differential testing. They need no second implementation and cover the representation-invariance faults that dominate heterogeneous ingestion.
8. Require every incident to produce an executable invariant, not only a test case.
9. Report pass^k alongside pass@k for anything whose output is acted upon.
10. Encode the L8 provenance invariants explicitly. They are the layer that lets the project claim it supports diagnosis of data-related faults rather than only detection.

**On the testbed.**

11. Build the record-replay layer first. Without it, topology comparisons are confounded by model variance and are not reproducible.
12. Use a factorial design over data version and agent configuration, so that data faults and model faults are separable by construction.
13. Instrument to the OpenTelemetry GenAI semantic conventions [29]. The conventions now cover agent, workflow, tool, and model spans plus latency and token metrics, and the MCP tracing layer. Two caveats: most attributes remain at development stability so names can change, and the dual-emission opt-in exists for exactly this transition; and prompt content belongs in span events rather than span attributes, because attributes are indexed, size-limited, and would place raw content including personal data into the observability backend where it cannot be filtered at the collector [29].
14. Complement telemetry with a provenance model. W3C PROV extensions with agent-centric entities exist for scientific workflow provenance and give formal causal lineage that telemetry conventions do not [30]. Given the project's existing knowledge-graph work, expressing run manifests and lineage as a graph rather than as flat logs is likely to be the lower-friction path and makes cross-run queries tractable.

**On human-in-the-loop.**

15. Design the triage queue around clustered mismatch signatures, not raw diffs.
16. Capture rationale and violated-property alongside every adjudication.
17. Measure and report inter-annotator agreement per dimension; treat it as a data-quality metric of the labelled set.

---

## 10. Gaps worth claiming as contributions

The survey surfaces several places where practice is thin and where Task 4.3 sits well.

**Adequacy measurement for data-quality suites.** There is no accepted way to say whether an expectation suite is good. Mutation testing applied to transformation code, with mutation score as the adequacy measure, is a direct and unclaimed transfer from classical software testing.

**Attribution of agent failures to data defects.** The observability literature is oriented toward finding where an agent went wrong in its own execution. Systematic attribution to an upstream data defect, via controlled data fault injection and a factorial design, is not established practice.

**Mismatch summarisation at relational scale.** LLMeDiff's honest limitation, that mismatches require manual root-cause analysis, becomes a bottleneck rather than an inconvenience once mismatches are row sets. Automated clustering of mismatch signatures into ranked, human-readable patterns is a tractable and useful engineering contribution.

**Metamorphic relations for harmonisation specifically.** The relations in 6.3 are assembled from general principles rather than from an existing catalogue. A validated catalogue of relations for heterogeneous-source harmonisation, with evidence about which relations detect which fault classes, does not appear to exist.

**Trajectory-level preference elicitation.** Preference data practice is oriented toward response pairs. What to show an annotator when the unit is a multi-step trajectory, and where to segment it, is unsettled.

---

## 11. Sources

1. M. Shah, "How to Test AI Agents", Medium, May 2026. https://medium.com/@mitesh_shah/how-to-test-ai-agents-40c79f3ddba9
2. E. Isaku, C. Laaber, H. Sartaj, S. Ali, T. Schwitalla, J. F. Nygård, "LLMs in the Heart of Differential Testing: A Case Study on a Medical Rule Engine", arXiv:2404.03664. https://arxiv.org/abs/2404.03664
3. S. Yao et al., "τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains", arXiv:2406.12045. https://arxiv.org/abs/2406.12045
4. sierra-research/tau2-bench. https://github.com/sierra-research/tau2-bench
5. T. Y. Chen et al., "Metamorphic Testing: A Review of Challenges and Opportunities", ACM Computing Surveys 51(1), 2018.
6. S. Segura, G. Fraser, A. B. Sanchez, A. Ruiz-Cortés, "A Survey on Metamorphic Testing", IEEE TSE 42(9), 2016.
7. "Bidirectional Empowerment of Metamorphic Testing and Large Language Models: A Systematic Survey", arXiv:2605.13898. https://arxiv.org/html/2605.13898v1
8. E. T. Barr, M. Harman, P. McMinn, M. Shahbaz, S. Yoo, "The Oracle Problem in Software Testing: A Survey", IEEE TSE 41(5), 2015.
9. W. M. McKeeman, "Differential Testing for Software", Digital Technical Journal 10(1), 1998.
10. "LLM Agent Evaluation Metrics in 2026: Tool Calling, Task Completion, Reasoning, and Trace-Based Evals", Confident AI. https://www.confident-ai.com/blog/llm-agent-evaluation-complete-guide
11. "AI agent evaluation: trajectory, tool calls, and task completion", Langfuse, July 2026. https://langfuse.com/resources/engineering/ai-agent-evaluation
12. "How to Measure Agent Trajectory: Metrics, Steps and Baselines", Atlan. https://atlan.com/know/ai-agent/ai-agent-trajectory-evaluation/
13. Data versioning tooling: DVC guide (https://casrai.org/guides/dvc-data-version-control-research-datasets-ml-pipelines), lakeFS (https://lakefs.io/blog/scalable-ml-data-version-control-and-reproducibility/), and a comparative overview of Dolt, DVC, Delta Lake, Iceberg, Hudi and lakeFS (https://medium.com/@okanyenigun/data-versioning-in-machine-learning-and-data-lakes-aa5ac3b6c33b)
14. Data validation tooling: Pandera and Great Expectations comparison (https://endjin.com/blog/a-look-into-pandera-and-great-expectations-for-data-validation), Great Expectations in pipelines (https://www.conduktor.io/glossary/great-expectations-data-testing-framework), lakehouse validation practice and the dbt-tests-run-after caveat (https://www.twicedata.com/labs/data-validation-with-great-expectations)
15. "Testing Pyramid for AI Agents", Block Engineering, January 2026. https://engineering.block.xyz/blog/testing-pyramid-for-ai-agents ; A. Jones, "Test Pyramid for AI Agents". https://angiejones.tech/test-pyramid-for-ai-agents/
16. "Deterministic Testing for LangChain Agents", Sixty North, April 2026. https://blog.sixty-north.com/deterministic-testing-for-langchain-agents.html
17. "Deterministic Replay: How to Debug AI Agents That Never Run the Same Way Twice", April 2026. https://tianpan.co/blog/2026-04-12-deterministic-replay-debugging-non-deterministic-ai-agents
18. S. Hyun, M. Guo, M. A. Babar, "METAL: Metamorphic Testing Framework for Analyzing Large-Language Model Qualities", arXiv:2312.06056.
19. A. Arcuri, L. Briand, "A Practical Guide for Using Statistical Tests to Assess Randomized Algorithms in Software Engineering", ICSE 2011.
20. "Synthetic Test Data for LLM Evaluation in 2026: A Practical Guide", Future AGI. https://futureagi.com/blog/synthetic-test-data-llm-evaluation-2026/
21. "Beyond Cooperative Simulators: Generating Realistic User Personas for Robust Evaluation of LLM Agents", arXiv:2605.12894. https://arxiv.org/html/2605.12894
22. "Agent World Model: Infinity Synthetic Environments for Agentic Reinforcement Learning", arXiv:2602.10090.
23. "ClawsBench: Evaluating Capability and Safety of LLM Productivity Agents in Simulated Workspaces", arXiv:2604.05172.
24. "ReliabilityBench: Evaluating LLM Agent Reliability Under Production-Like Stress Conditions", arXiv:2601.06112 (action metamorphic relations); "AgentNoiseBench", arXiv:2602.11348 (trajectory-aware evaluation protocol).
25. "Multi-Agent LLM-based Metamorphic Testing for REST APIs", arXiv:2605.28321. https://arxiv.org/html/2605.28321
26. "Data Labeling for LLMs: Annotation Methods, Quality and Governance", Atlan. https://atlan.com/know/data-labeling-best-practices-llms/
27. "Incentivizing High-Quality Human Annotations with Golden Questions", arXiv:2505.19134 (survey of reported preference-noise levels); "AIR: A Systematic Analysis of Annotations, Instructions, and Response Pairs in Preference Datasets", arXiv:2504.03612.
28. "RLHF Data Collection: Preference Datasets for LLMs". https://aitaggers.com.au/blog/rlhf-data-collection-guide ; "RLHF Training Data and LLM Fine-Tuning: The 2026 Practitioner's Guide". https://www.dataxpower.com/blog/rlhf-training-data-llm-fine-tuning
29. OpenTelemetry Semantic Conventions for Generative AI Systems; "Inside the LLM Call: GenAI Observability with OpenTelemetry", May 2026 (https://opentelemetry.io/blog/2026/genai-observability/); "How OpenTelemetry Traces LLM Calls, Agent Reasoning, and MCP Tools" (https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions)
30. PROV-AGENT, W3C PROV extended with agent-centric entities for scientific workflow provenance, as discussed in arXiv:2603.21692.
31. OWASP Top 10 for LLM Applications. https://genai.owasp.org/llm-top-10/ ; Microsoft PyRIT. https://github.com/microsoft/PyRIT
