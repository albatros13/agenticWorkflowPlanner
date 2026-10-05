# Implementation Instructions: Guideline-to-BPMN Agent

## Goal

Implement an agent that converts natural-language process descriptions, especially healthcare clinical guidelines, into accurate, auditable BPMN 2.0 process models.

The system must **not** perform direct `text → BPMN` generation. Use a staged, validated pipeline:

```text
Source document
  → evidence extraction
  → clinical/process logic extraction
  → structured intermediate representation (IR)
  → BPMN generation
  → syntactic + semantic validation
  → critique/repair
  → scenario/execution validation
  → human review
```

The LLM is a semantic translator/reasoner. Deterministic software should construct and validate BPMN whenever possible.

---

# Step 1 — Establish the project architecture

Create clear modules for:

```text
ingestion/
retrieval/
evidence/
logic/
ir/
terminology/
bpmn/
validation/
critique/
simulation/
provenance/
evaluation/
api/
tests/
```

Keep domain-independent process modelling separate from healthcare-specific terminology and validation.

### Requirements

- Python 3.11+ unless the existing project dictates otherwise.
- Use typed models (`pydantic` or equivalent).
- Make model/provider access replaceable through an interface.
- Keep prompts/versioned configuration outside core business logic.
- Make every pipeline stage independently executable and testable.
- Store intermediate artifacts so failures can be inspected.

### Test

Add a smoke test proving that a minimal input can pass through every pipeline stage using a mocked LLM.

### Validation

The pipeline must fail explicitly if a required intermediate artifact is missing. Never silently continue with incomplete data.

---

# Step 2 — Implement document ingestion and evidence extraction

Accept natural-language process descriptions and documents such as:

- clinical guidelines
- protocols
- institutional procedures
- regulatory procedures
- scientific workflow descriptions.

Extract structured evidence chunks.

Each evidence item should contain at least:

```json
{
  "id": "E12",
  "text": "...",
  "source_document": "...",
  "section": "...",
  "page": 12,
  "location": "...",
  "confidence": 0.97
}
```

Preserve exact source locations whenever available.

### Requirements

- Support PDF/text input through an abstraction.
- Preserve document structure: headings, paragraphs, tables where possible.
- Chunk semantically rather than only by token count.
- Retrieval must return the original evidence text and location.
- Do not allow the LLM to answer from memory when evidence is available.

### Tests

Create fixtures containing:

1. Simple sequential guideline.
2. Guideline with an XOR decision.
3. Guideline with AND/concurrent activities.
4. Guideline with temporal constraints.
5. Guideline containing an exception.
6. Guideline containing ambiguous language.

Verify that the relevant source passage can be retrieved for each case.

### Validation

Every clinically meaningful process element produced later must be traceable to one or more evidence IDs.

---

# Step 3 — Build the intermediate representation (IR)

Do **not** generate BPMN directly from text.

Define a structured process IR containing at least:

```text
Process
  id
  name
  description
  actors
  elements
  relations
  evidence
  uncertainties
```

Elements should support:

```text
Start
End
Activity
Decision
Gateway
Event
Subprocess
DataObject
Message
```

Each element should support:

```text
id
type
name
description
actor
inputs
outputs
conditions
temporal_constraints
evidence_refs
confidence
status
```

Relations should support:

```text
sequence
parallel
conditional
message
data
dependency
```

### Important semantic fields

Represent explicitly:

- AND
- OR
- XOR
- eligibility criteria
- thresholds
- temporal relationships
- repetition/loops
- escalation
- exceptions
- contraindications
- missing information
- alternative treatments
- termination criteria.

### Uncertainty model

Every inferred item must be classified as:

```text
EXPLICIT
DERIVED
INFERRED
AMBIGUOUS
MISSING
```

Do not represent unsupported assumptions as explicit facts.

### Tests

Unit-test serialization/deserialization.

Test that:

- invalid element types are rejected;
- missing evidence references are rejected;
- malformed gateways are rejected;
- uncertainty states are preserved;
- round-tripping JSON does not change semantics.

---

# Step 4 — Implement semantic extraction from evidence

Implement an LLM agent that converts retrieved evidence into the IR.

Use structured output/function calling rather than free-form prose.

The extraction prompt must explicitly instruct the model:

1. Extract what the source says.
2. Do not invent clinical actions.
3. Separate explicit statements from logical derivations.
4. Preserve conditions and thresholds exactly.
5. Preserve temporal constraints.
6. Record ambiguity rather than resolving it silently.
7. Attach evidence references to every extracted element.

### Two-pass strategy

Use at least two distinct passes:

```text
Pass A: factual/evidence extraction
Pass B: process/logic interpretation
```

Do not ask one prompt to simultaneously read the document, invent the process structure, and render BPMN.

### Tests

Create expected IR fixtures for the simple guideline cases.

Measure:

- activity recall
- decision recall
- condition preservation
- actor preservation
- ordering preservation
- evidence coverage.

### Validation

Reject an IR element that has no evidence reference unless it is explicitly marked `DERIVED` and contains references to the source elements from which it was derived.

---

# Step 5 — Add terminology and ontology grounding

Introduce a terminology resolver.

For healthcare processes, support configurable mappings to appropriate controlled vocabularies, for example:

- SNOMED CT
- LOINC
- RxNorm
- ATC
- ICD where appropriate.

Do not require an ontology match when none exists.

Represent:

```text
surface_form
normalized_label
ontology
concept_id
match_type
confidence
```

### Rules

- Never silently replace the original wording.
- Preserve the source term alongside normalized terminology.
- Low-confidence mappings must remain reviewable.
- Terminology normalization must not change clinical meaning.

### Tests

Include synonym and ambiguous-term fixtures.

Verify that normalization does not alter:

- drug identity;
- dosage;
- threshold;
- anatomical site;
- disease state;
- test interpretation.

---

# Step 6 — Implement deterministic BPMN generation

Create a compiler:

```text
Process IR → BPMN 2.0
```

Do not ask the LLM to write arbitrary BPMN XML.

The compiler should deterministically map:

```text
Activity       → task
Decision       → gateway / conditional flow
Start          → start event
End            → end event
Actor          → pool/lane
Parallelism    → parallel gateway
XOR            → exclusive gateway
OR             → inclusive gateway
Message        → message flow
Data           → data object/association
```

Use a BPMN library or a well-defined BPMN 2.0 serializer.

### Requirements

- Stable IDs.
- Valid sequence flows.
- Explicit gateway semantics.
- Explicit start/end events.
- Correct lane/actor assignment.
- No orphan nodes.
- No impossible sequence-flow structures.

### Tests

Compile every IR fixture to BPMN.

Run:

- XML validation;
- BPMN schema validation;
- parser round-trip;
- graph connectivity tests.

---

# Step 7 — Implement structural BPMN validation

Create deterministic validators for:

### Syntax

- valid XML;
- BPMN 2.0 conformance;
- valid element types;
- valid references.

### Graph structure

Detect:

- unreachable nodes;
- dead ends;
- orphan tasks;
- missing start events;
- missing end events;
- invalid gateway structures;
- sequence flows with invalid endpoints;
- impossible loops;
- inconsistent joins/splits.

### Semantic structure

Check:

- every activity has an actor where required;
- every decision has outgoing conditions;
- exclusive branches are mutually interpretable;
- parallel branches have a corresponding join when required;
- every meaningful process element has evidence.

### Tests

Create deliberately broken BPMN models and ensure each validator detects the intended defect.

---

# Step 8 — Implement source-to-process coverage validation

Build a coverage checker comparing evidence against the IR/BPMN.

Produce metrics such as:

```text
Evidence coverage
Activity coverage
Decision coverage
Condition coverage
Actor coverage
Temporal-constraint coverage
Exception coverage
```

Example output:

```json
{
  "evidence_coverage": 0.96,
  "decision_coverage": 1.0,
  "condition_coverage": 0.91,
  "unsupported_elements": 1,
  "unmapped_evidence_items": 3
}
```

### Tests

Create examples with:

- deliberately omitted activity;
- deliberately invented activity;
- missing condition;
- incorrect ordering.

The validator must identify each problem.

---

# Step 9 — Add an independent critic agent

Implement a separate critique stage:

```text
Source + evidence + generated IR/BPMN
                  ↓
              Critic
                  ↓
        structured findings
```

The critic should look specifically for:

- hallucinated activities;
- omitted activities;
- incorrect sequence;
- incorrect gateway type;
- incorrect conditions;
- incorrect actors;
- unsupported assumptions;
- missing exceptions;
- terminology errors.

Return structured findings:

```json
{
  "severity": "ERROR",
  "element_id": "A7",
  "problem": "Condition differs from source",
  "evidence_refs": ["E21"],
  "recommendation": "Restore threshold from E21"
}
```

### Important

The critic should have access to the original evidence, not merely the generated diagram.

### Tests

Seed known defects and verify that the critic identifies them.

---

# Step 10 — Implement repair/clarification loops

Use:

```text
Generate
  ↓
Validate
  ↓
Critique
  ↓
Repair
  ↓
Validate again
```

Set a maximum number of repair iterations.

If unresolved ambiguity remains, return a clarification request rather than repeatedly guessing.

Example:

```text
AMBIGUITY:
The guideline states that treatment should be "considered"
but does not specify the decision criterion.

Required action:
Clinical/user clarification.
```

### Tests

Verify:

- valid models are not unnecessarily modified;
- known errors are repaired;
- unresolved ambiguity terminates safely;
- repair loops cannot run indefinitely.

---

# Step 11 — Add provenance and bidirectional traceability

Maintain:

```text
Guideline sentence
       ↕
Evidence item
       ↕
Clinical/process rule
       ↕
IR element
       ↕
BPMN element
```

Expose this through the API and UI.

Users should be able to ask:

- Why is this task present?
- Which guideline passage created this decision?
- Why is this an XOR gateway?
- Which source supports this condition?
- Which source statements are not represented?

### Tests

For every BPMN element in the test fixtures, verify that provenance can be followed back to source evidence.

---

# Step 12 — Implement scenario-based execution validation

Create an executable/simulation representation of the process.

Generate synthetic cases covering:

- normal path;
- each decision branch;
- boundary values;
- missing data;
- contraindications;
- exceptions;
- escalation;
- treatment failure;
- repeated actions.

Compare expected outcomes against the reference logic.

### Requirements

The system must distinguish:

```text
structurally valid
semantically plausible
clinically validated
```

These are not equivalent.

### Tests

For each fixture, define expected outcomes for representative scenarios.

The generated BPMN should produce the expected outcome.

---

# Step 13 — Build the evaluation framework

Implement automated metrics.

## Structural metrics

- BPMN validity
- graph connectivity
- Graph Edit Distance (GED/RGED)
- node/edge precision and recall.

## Semantic metrics

- activity precision/recall
- decision precision/recall
- condition accuracy
- ordering accuracy
- actor accuracy
- exception coverage
- temporal-constraint accuracy.

## Provenance metrics

- evidence coverage
- unsupported-element rate
- traceability completeness.

## Execution metrics

- scenario agreement
- decision agreement
- outcome agreement.

Report metrics independently; do not collapse everything into one score.

---

# Step 14 — Build a gold-standard benchmark

Create a benchmark containing representative process descriptions.

For each case store:

```text
source document
annotated evidence
gold process logic
gold BPMN
gold scenarios
expected outcomes
```

Prefer clinician/domain-expert annotation for healthcare cases.

Include difficult cases rather than only simple sequential workflows.

### Benchmark categories

At minimum:

1. Sequential process.
2. XOR branching.
3. AND/concurrent work.
4. Nested decisions.
5. Loops.
6. Temporal constraints.
7. Exceptions.
8. Missing/ambiguous information.
9. Multiple actors.
10. Complex clinical guideline.

### Validation

Run the complete pipeline against the benchmark after every change to prompts, models, schemas, or compiler logic.

---

# Step 15 — Add regression testing

Create a single command that runs:

```text
unit tests
→ IR tests
→ BPMN validation
→ provenance tests
→ semantic evaluation
→ scenario tests
→ benchmark comparison
```

Store results by:

```text
model
model_version
prompt_version
pipeline_version
benchmark_version
timestamp
```

Fail CI if critical metrics regress beyond configured thresholds.

---

# Step 16 — Implement human review

Provide a review state:

```text
DRAFT
→ MACHINE_VALIDATED
→ NEEDS_CLARIFICATION
→ HUMAN_REVIEW
→ APPROVED
```

Do not label a model "clinically correct" solely because an LLM critic approves it.

The reviewer must be able to inspect:

- BPMN;
- source evidence;
- provenance;
- uncertainties;
- validation errors;
- critic findings;
- scenario results.

---

# Step 17 — Implement reproducibility and audit logging

Persist:

- input document hash;
- evidence chunks;
- retrieval configuration;
- model/provider/version;
- prompts;
- structured LLM outputs;
- IR versions;
- BPMN versions;
- validation results;
- critic results;
- repair iterations;
- human decisions.

Never overwrite previous versions.

---

# Step 18 — Security and privacy

For healthcare documents:

- minimize data sent to external LLMs;
- support local/private models where possible;
- redact unnecessary patient-identifying information;
- never log sensitive source text unnecessarily;
- make provider selection configurable;
- keep secrets outside prompts and source files.

Add tests ensuring sensitive values are not written to ordinary application logs.

---

# Step 19 — API and user experience

Expose an API resembling:

```text
POST /processes
POST /processes/{id}/extract
POST /processes/{id}/generate
POST /processes/{id}/validate
POST /processes/{id}/critique
POST /processes/{id}/repair
GET  /processes/{id}/bpmn
GET  /processes/{id}/provenance
GET  /processes/{id}/validation
GET  /processes/{id}/uncertainties
POST /processes/{id}/approve
```

The UI should show BPMN alongside source evidence and allow selecting a BPMN element to reveal its provenance.

---

# Step 20 — Final acceptance criteria

The implementation is complete only when all of the following are true:

- [ ] No direct LLM-to-BPMN generation is used as the primary path.
- [ ] A structured IR exists between language and BPMN.
- [ ] Every meaningful process element has provenance.
- [ ] Explicit, derived, inferred, ambiguous and missing information are distinguished.
- [ ] BPMN is deterministically compiled from the IR.
- [ ] BPMN syntax and graph structure are automatically validated.
- [ ] Source/process semantic coverage is measured.
- [ ] An independent critic can identify semantic errors.
- [ ] Repair is bounded and deterministic at the orchestration level.
- [ ] Ambiguity results in clarification rather than hallucinated completion.
- [ ] Scenario-based execution testing exists.
- [ ] A gold-standard benchmark exists.
- [ ] Regression testing is automated.
- [ ] Human review is required for final clinical approval.
- [ ] Full provenance and audit history are retained.
- [ ] Sensitive healthcare data is handled safely.

# Development principle

At every implementation stage prefer:

**evidence over model memory, structure over free text, deterministic compilation over generation, validation over trust, provenance over explanation-after-the-fact, and human clarification over unsupported inference.**

The final system should behave like a **verified process compiler with an LLM semantic front-end**, not like a chatbot that draws BPMN.
