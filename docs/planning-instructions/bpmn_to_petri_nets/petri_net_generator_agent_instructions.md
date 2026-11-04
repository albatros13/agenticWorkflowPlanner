# Coding-agent instructions: Petri Net Generator Agent

Related work: https://pcoz.github.io/formally-verified-learnable-process-intelligence/api/

## Goal

Implement a **Petri Net Generator worker agent** for the existing
FastAPI multi-agent application built with **Pydantic AI**. The user
interacts with the system through a coordinator agent; each worker agent
runs as a separate service on its own port.

The worker must support two input paths:

1.  **Text specification → validated intermediate representation (IR) →
    ordinary workflow Petri net → PNML**
2.  **BPMN 2.0 XML specification → ordinary workflow Petri net → PNML**

Reuse mature libraries wherever possible. Do not implement a full
Petri-net engine or BPMN parser from scratch unless testing shows that
the existing tools cannot meet a required semantic case.

## Recommended implementation strategy

### Primary recommendation

Use **PM4Py as an in-process Python library inside the Petri Net
Generator worker service**. PM4Py supports BPMN import, conversion to Petri nets, and PNML
import/export. Its documented BPMN support includes start/end events,
tasks, and exclusive, parallel, and inclusive gateways; do not assume
every BPMN 2.0 feature is supported.

-   PM4Py API reference:
    <https://github.com/process-intelligence-solutions/pm4py/blob/release/docs/source/api.rst>
-   BPMN support and documented limitations:
    <https://processintelligence.solutions/pm4py/examples/bpmn-support>

For the text path, use **Pydantic AI structured output** to produce a
typed process IR, then compile the IR deterministically into a Petri
net. Prefer an IR based on a small structured process algebra (sequence,
exclusive choice, parallel split/join, and loop) rather than asking an
LLM to invent places, transitions, arcs, and markings directly. This
makes the result easier to validate, test, explain, and repair.

The worker should be a separate FastAPI service on its own configured
port. The coordinator calls its
public API using the same service-discovery/client pattern as the other
workers. Keep conversion and validation logic in a Python package so it
can be tested without starting the HTTP server.

### When to use an external service

Do **not** deploy WoPeD or another converter as a separate service by
default. First use PM4Py in-process. Run an external process/service
only if: - PM4Py lacks a required BPMN construct or conversion
behavior; - a converter has a useful API/CLI not available as a
library; - isolation is required for reliability or dependency
management.

A small open-source web converter also exists at
<https://github.com/BenjaNapo/bpmn-to-petri> and advertises PNML/DOT
export. Treat it as an optional candidate: assess maintenance, test coverage, BPMN coverage, security,
and license before integrating it.

## Architecture

``` text
User
  |
  v
Coordinator agent (existing Pydantic AI multi-agent system)
  |
  | HTTP request to configured worker URL
  v
Petri Net Generator FastAPI worker (own port)
  |
  +-- Text input
  |     -> Pydantic AI text-to-IR step
  |     -> Pydantic validation + semantic checks
  |     -> deterministic IR-to-Petri-net compiler
  |
  +-- BPMN XML input
  |     -> secure XML input checks
  |     -> PM4Py BPMN importer
  |     -> PM4Py BPMN-to-Petri-net conversion
  |
  +-- Common Petri-net validation
  |     -> PNML serialization
  |     -> optional JSON summary / DOT visualization
  |
  v
Response to coordinator: status, model summary, warnings, PNML content or artifact reference
```

Use existing project conventions for configuration, logging, errors,
authentication, tracing, and service-to-service HTTP. Do not create a
second agent framework or bypass the coordinator.

## Functional requirements

### 1. Input contract

Expose a typed API with an explicit `input_type` discriminator. Support
either JSON text input or uploaded/provided BPMN XML. Prefer a JSON
endpoint for the first implementation; add multipart upload only if the
existing app already uses it.

Example request for text:

``` json
{
  "input_type": "text",
  "text": "A request is submitted and validated. If valid, approve it and execute it. If invalid, return it for revision and validate it again.",
  "output_formats": ["pnml"]
}
```

Example request for BPMN XML:

``` json
{
  "input_type": "bpmn_xml",
  "bpmn_xml": "<?xml version=\"1.0\"?><definitions ...>...</definitions>",
  "output_formats": ["pnml"]
}
```

Also support a `model_name` or `title` field if consistent with project
conventions. Reject requests where the required field for the chosen
input type is absent. Set configurable limits for input size and
execution time.

### 2. Intermediate representation for text input

Define the IR using Pydantic models. Keep it small, explicit, versioned,
and independent of PM4Py's internal classes.

Minimum suggested structure:

-   `schema_version`
-   `process_name`
-   `description`
-   `start`
-   `nodes` or a recursive `root` expression
-   optional `metadata` and `assumptions`

Prefer a recursive expression model with discriminated variants:

-   `activity`: one atomic task with a stable ID and human-readable
    label
-   `sequence`: ordered list of expressions
-   `xor`: exclusive choice with two or more branches and optional
    branch conditions
-   `parallel`: concurrent branches that must synchronize before
    continuing
-   `loop`: body plus an explicit exit/continue condition or structured
    repeat semantics

Use a typed Pydantic discriminated union for these expression types.
Validate IDs, non-empty labels, branch counts, maximum nesting depth,
and maximum node count. Do not silently accept unsupported constructs.

If a flat graph IR is more compatible with the existing application, it
may be used instead, but it must explicitly encode nodes, directed
edges, gateway semantics, entry/exit points, and conditions. Do not
represent XOR and AND merely as untyped graph forks: their semantics
differ.

### 3. Text → IR using Pydantic AI

Create a dedicated Pydantic AI agent or reuse the project's established
model/provider configuration. Its only task is to transform the user's
process description into the typed IR.

Instructions to the extraction agent: - Preserve the described order,
alternatives, parallelism, and loops. - Do not invent business steps,
conditions, actors, or outcomes. - If a necessary branch condition or
loop termination condition is unclear, record an assumption or return a
clarification requirement rather than fabricate it. - Distinguish
exclusive choice (XOR) from concurrency (AND). - Treat "then" as
sequence, "either/or" as exclusive choice when supported by the text,
and "in parallel/both" as concurrency. - Return only the required
structured model, not Petri-net XML. - Include unresolved ambiguities in
a dedicated list with severity and a question the coordinator can ask
the user.

The coordinator should ask the user to clarify blocking ambiguities
before generation. Non-blocking assumptions must be returned to the user
with the generated model.

Do not rely on the LLM for formal correctness. The output must pass
Pydantic validation and deterministic semantic checks before
compilation.

### 4. IR → ordinary workflow Petri net

Generate a **place/transition Petri net** with ordinary unweighted arcs
and explicit initial and final markings. Do not introduce colored
tokens, timed transitions, stochastic rates, inhibitor arcs, or reset
arcs.

Use PM4Py's Petri-net data structures and PNML exporter if practical,
but keep the IR compiler isolated behind an adapter. The compiler must
preserve the control-flow semantics of the IR.

Required semantics: - **Activity:** model task execution as a
transition, with input and output places. - **Sequence:** the completion
place of one expression feeds the next expression. - **XOR split/join:**
represent exclusive alternatives without accidentally enabling multiple
alternatives as concurrent work. - **AND split/join:** allow all
parallel branches to execute and synchronize before the process
continues. - **Loop:** represent repeat/exit behavior explicitly and
test termination paths. - **Start/end:** produce a well-defined source
place with one initial token and a well-defined sink place as the final
marking.

If PM4Py's process-tree representation is the most reliable route for a
particular construct, map the IR to a process tree and use PM4Py
conversion. Otherwise implement a small, deterministic compiler from the
supported IR constructs to PM4Py Petri-net objects. Avoid mixing
compiler logic into the API route or LLM prompt.

The resulting net should be a workflow net where possible: one source
place, one sink place, and all nodes on a path from source to sink.
Report when this property cannot be established.

### 5. BPMN XML → Petri net

Use PM4Py's documented BPMN path as the first implementation:

1.  Validate the input size and XML structure.
2.  Parse the BPMN XML using PM4Py's BPMN reader or its supported object
    API.
3.  Convert the BPMN model to an accepting Petri net, including initial
    and final markings.
4.  Run common validation and serialize to PNML.

Use the installed PM4Py version's actual API signatures and add an
integration test against the pinned version. Common documented calls
include `pm4py.read_bpmn(...)`, `pm4py.convert_to_petri_net(...)`, and
`pm4py.write_pnml(...)`; verify exact signatures and whether the
functions accept file paths, objects, or both in the version selected.

BPMN support is limited. Start with: - start and end events; - ordinary
tasks; - sequence flows; - exclusive gateways; - parallel gateways; -
inclusive gateways only if conversion behavior is verified by tests.

For unsupported or ambiguous BPMN constructs---such as boundary events,
event-based gateways, compensation, transactions, complex gateways,
message flows, subprocesses, or complex event semantics---return a
structured unsupported-feature warning or a clear error. Do not silently
drop elements or claim semantic equivalence. Detect malformed XML and
missing start/end flows.

Use a secure XML parser/configuration. Disable DTDs and external entity
resolution; do not fetch external resources referenced by user-supplied
XML. Enforce payload and processing limits.

### 6. Common validation

Before returning a generated net, perform structural checks: - every arc
references an existing place and transition; - no duplicate IDs; -
initial and final markings reference existing places; - the initial and
final markings are well-defined; - source and sink are identified; - all
nodes are reachable from the source and can reach the sink, or else
report exceptions; - no unsupported arc/transition types are emitted; -
PNML can be parsed back into a net.

Where supported and computationally reasonable, offer an optional
soundness check. Do not label a net "sound" merely because it parses or
is connected. A full soundness check may be expensive; apply timeouts
and return `not_checked` rather than blocking indefinitely.

Return validation findings as structured data with severity (`error`,
`warning`, `info`), code, message, and affected element IDs.

### 7. Output formats

**Required: PNML.** Generate PNML using the PM4Py exporter and
include: - places and transitions; - arcs; - initial marking; - final
marking if supported by the selected PNML profile; - stable element IDs
and readable labels.

PNML compatibility can vary between tools. Add round-trip tests using
PM4Py and at least one target consumer (e.g., WoPeD) if the project can
run it in CI. Keep PNML serialization behind an exporter interface.

Alternative outputs to list or optionally implement: - **JSON IR** ---
best for editing, provenance, regeneration, and communication between
agents; not a universal Petri-net interchange format. - **DOT/Graphviz**
--- simple graph visualization; does not by itself preserve all
Petri-net semantics or markings unless explicitly encoded. - **SVG** ---
convenient preview, typically generated from DOT or a visualization
library. - **PTML** --- PM4Py process-tree interchange format, useful if
the text route is represented as a process tree; not a replacement for
PNML. - **BPMN 2.0 XML** --- optional reverse conversion for
process-model interchange, but not guaranteed for every net. - **Native
JSON response summary** --- useful for the coordinator and UI; include
counts, labels, markings, validation, assumptions, and warnings.

Do not make all formats mandatory for the first release. Implement PNML
plus a JSON summary first; add DOT/SVG if the UI needs a preview.

### 8. API and response design

Suggested routes (adapt names to project conventions):

-   `POST /generate` --- generate from text or BPMN XML.
-   `POST /validate` --- validate a supplied IR or PNML if useful.
-   `GET /health` --- health/readiness endpoint.

A successful response should include: - `status` - `input_type` -
`process_name` - `net_summary` (place count, transition count, arc
count, source/sink IDs) - `initial_marking` and `final_marking` -
`validation` (findings and whether soundness was checked) -
`assumptions` - `warnings` - `ir` for text input, where useful - `pnml`
or an artifact reference - optional `dot` or `svg` only when requested

Avoid returning large PNML payloads by default if the application
already has artifact/file storage. In that case return a durable
artifact ID or URL using existing infrastructure. If there is no
artifact storage, return PNML as a string for small outputs and enforce
a size limit.

Use typed Pydantic request/response models and meaningful HTTP status
codes. Keep detailed stack traces out of client responses; log them
internally with a correlation/request ID.

### 9. Multi-agent integration

The coordinator remains the user-facing agent. Register the new worker
using the project's existing worker discovery and invocation
conventions.

The worker should expose a concise tool description, for example:

> Generate an ordinary workflow Petri net from a natural-language
> process description or BPMN 2.0 XML. For text input, produce and
> validate an intermediate process representation first. Return PNML,
> model summary, assumptions, and validation findings. Ask for
> clarification when essential process semantics are ambiguous.

Coordinator behavior: 1. Identify whether the user supplied text or BPMN
XML. 2. Collect missing input and requested output format. 3. Call the
Petri Net Generator worker over internal HTTP using its configured base
URL. 4. If the worker returns blocking ambiguity findings, ask the user
a targeted clarification question and retry with the answer. 5. Present
the generated PNML/artifact, summary, assumptions, warnings, and
supported next steps. 6. Never claim formal soundness unless the worker
reports that the relevant check completed successfully.

Service integration: - Configure `PETRI_NET_AGENT_BASE_URL` (or the
existing service-registry equivalent), not a hard-coded host/port. -
Reuse the existing HTTP client, authentication, timeout, retry, tracing,
and error-handling utilities. - Use a bounded request timeout and avoid
retrying non-idempotent generation blindly. If generation is
deterministic from the same request, support a request/correlation ID
and optional idempotency key. - Keep model API credentials in the
existing secret-management mechanism; never pass provider keys from the
coordinator in user-visible payloads. - Ensure the worker can be
deployed and tested independently.

## Suggested package layout

Adapt this to the repository's actual conventions; do not create a
parallel application structure if equivalent modules already exist.

``` text
petri_net_agent/
  app.py                 # FastAPI app and route registration
  config.py              # environment-backed settings
  schemas.py              # request/response and IR Pydantic models
  agent.py                # Pydantic AI text-to-IR agent
  ir_validation.py        # deterministic IR checks
  compiler.py             # IR -> Petri net
  bpmn_converter.py       # BPMN -> Petri net via PM4Py
  net_validation.py       # graph, markings, reachability checks
  exporters.py            # PNML and optional DOT/SVG
  service.py              # orchestration of conversion pipeline
  tests/
    test_ir_schema.py
    test_text_to_ir.py
    test_ir_to_net.py
    test_bpmn_conversion.py
    test_pnml_roundtrip.py
    test_validation.py
    fixtures/
```

## Testing and acceptance criteria

Implement automated tests with small, known examples. Tests must assert
semantics, not merely that a file was created.

### Text path

-   Simple sequence: submit → validate → approve → finish.
-   XOR: valid/invalid alternatives remain mutually exclusive.
-   AND: both branches must complete before continuation.
-   Loop: revision/revalidation can repeat and can also terminate.
-   Ambiguous condition: worker reports a clarification requirement
    rather than inventing a branch.
-   Invalid IR: rejected before compilation.

### BPMN path

-   Minimal start → task → end model.
-   Exclusive gateway with two branches and a merge.
-   Parallel split/join.
-   Unsupported BPMN element returns a clear warning/error.
-   Malformed XML and oversized payload are rejected safely.

### Export and validation

-   PNML output is well-formed XML.
-   PM4Py can read the generated PNML back.
-   Round-tripped net retains places, transitions, arcs, initial
    marking, and final marking.
-   Invalid markings and dangling arcs are caught.
-   Disconnected/dead-end nodes are reported.
-   Tests distinguish "structurally valid" from "soundness checked and
    passed."

### Service integration

-   `/health` responds successfully.
-   `/generate` returns typed responses for both input modes.
-   Coordinator can call the worker using configured service URL.
-   Timeout, conversion error, unsupported feature, and ambiguous text
    are handled gracefully.
-   No credentials, full stack traces, or unsafe XML details leak to
    clients.

## Implementation sequence

1.  Inspect the repository and identify the existing worker-agent
    pattern, Pydantic AI version, configuration conventions, HTTP
    client, logging, and test setup.
2.  Add the worker service skeleton and typed request/response schemas.
3.  Pin a compatible PM4Py version and verify the installed BPMN import,
    conversion, and PNML export APIs with a minimal integration test.
4.  Implement BPMN-to-Petri-net conversion and PNML round-trip tests
    first; this validates the main library choice.
5.  Define the IR and implement deterministic IR validation.
6.  Implement text-to-IR using Pydantic AI structured output.
7.  Implement and test IR-to-net compilation for sequence, XOR, AND, and
    loops.
8.  Add common net validation and structured warnings.
9.  Register the worker with the coordinator and document its URL/port
    configuration.
10. Add optional visualization only after the PNML path is reliable.
11. Update project documentation with supported BPMN constructs, known
    limitations, environment variables, local run instructions, and
    examples.

## Definition of done

-   Both text and BPMN input paths work end-to-end.
-   Text is converted to a typed IR before net generation.
-   The result is an ordinary workflow Petri net with explicit
    initial/final markings.
-   PNML is the primary supported export.
-   Unsupported BPMN semantics and ambiguous text are surfaced rather
    than silently approximated.
-   PM4Py is reused for net structures, BPMN conversion, and PNML export
    where appropriate.
-   The worker runs on its own configured port and integrates with the
    existing coordinator.
-   Automated tests cover core control-flow semantics and PNML round
    trips.
-   Documentation states what has and has not been validated.

## References

-   PM4Py API reference (BPMN, PNML, conversion):
    <https://github.com/process-intelligence-solutions/pm4py/blob/release/docs/source/api.rst>
-   PM4Py BPMN support and limitations:
    <https://processintelligence.solutions/pm4py/examples/bpmn-support>
-   Optional BPMN-to-Petri web converter (evaluate before adopting):
    <https://github.com/BenjaNapo/bpmn-to-petri>
