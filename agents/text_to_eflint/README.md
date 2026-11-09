# text_to_eflint — eFLINT generation

The `text_to_eflint` agent can generate eFLINT specifications from regulatory text. An LLM drafts
the specification. The code then checks it, runs its scenarios, and sends any errors back to the
LLM for repair. See [`RUN.md`](../../RUN.md#eflint-generator-regulatory-text---executable-eflint)
for how to run it.

## How it works

The code lives in [`generation/`](generation/):

- **Draft:** the LLM returns a structured spec: facts, duties, acts, instances, initial state and
  scenarios. Conditions are written in eFLINT syntax. Every non-atomic declaration must cite its
  article and copy a verbatim quote from the input text.
- **Check:** the code parses and type-checks the spec, confirms each quote really appears in the
  input, and runs the scenarios with a small built-in eFLINT simulator. Errors go back to the LLM
  for repair, up to 2 rounds by default. A repair that makes things worse is thrown away.
- **Output:** files laid out like the upstream framework (`Ontology.eflint`, `Norms.eflint`,
  `Init.eflint`, one `Scenario-<n>.eflint` per scenario), plus a single self-contained
  `spec.eflint`.

## What is reused

- **Upstream framework:** the Scattered-Directive *data governance framework* (MIT licence) is
  copied into [`generation/reference/`](generation/reference/), with its source and commit
  recorded. The code reads those files directly. They are the worked example in the LLM prompt
  and the source of the golden tests. The modelling instructions in the prompt follow the same
  approach.
- **Existing agent code:** the FlintFiller labeller ([`labelling/`](labelling/)) can optionally
  pre-label normative sentences as hints for the LLM (`srl_hints`, off by default because it
  costs one LLM call per sentence). The agent can also read a document from the
  `regulatory_docs` library, optionally narrowed to some sections. It uses the same LLM provider
  settings as `text_to_bpmn`.

## Where it plugs in

- An agent handler ([`agent.py`](agent.py)) with two intents: `generate_eflint`, and
  `validate_eflint` (checks a spec and runs its scenarios, no LLM).
- A standalone service ([`service.py`](service.py)) on port 8091 (`/generate`, `/validate`).
- A "Generate eFLINT" button in the demo UI ([`demo/`](demo/)). "Load sample" offers four Dutch
  regulatory-text samples from the DMI Afsprakenstelsel. After generating from a sample, the demo
  compares the result with the sample's reference formalisation.
- A generation-quality evaluation ([`evaluation/`](evaluation/)). It uses the vendored DMI
  formalisation ([`evaluation/dmi/README.md`](evaluation/dmi/README.md)), a structural comparator
  and a QA runner (`python -m agents.text_to_eflint.evaluation.qa --out <dir>`). The method and
  results are in [`docs/test_to_eflint/QA_EVAL.md`](../../docs/test_to_eflint/QA_EVAL.md).
- Registration in `config/agents.json`, `config/storage.json` and the coordinator factory.
- Run instructions in [`RUN.md`](../../RUN.md).

## Verification

- **Golden tests:** all five upstream scenarios run through the simulator. Every result matches
  what the author wrote in the comments ("// False", "fails", "does not hold"). The upstream spec
  also survives a round trip: load, render, re-parse, and the scenarios give the same results.
- **Test suite:** `tests/text_to_eflint/`, all offline (scripted LLM provider), plus the opt-in
  live quality test `test_dmi_quality_live.py` (`EFLINT_QA_LIVE=1`).
- **Live run:** on GDPR Art. 6(1)(a), 7(3) and 17(1)(b) with Claude Sonnet, generation succeeded
  after 2 repair rounds, with 4/4 scenarios passing and no grounding warnings. It took about
  2.5 minutes.

## Limitations

- **Not checked by the official eFLINT interpreter:** the built-in simulator is the only check,
  and it covers only the subset of eFLINT the generator emits.
- **Interpretation choice:** when a String-based type has a `Holds when` condition (like
  `primary-controller`), the simulator treats it as true only for instances that were explicitly
  asserted. That is what makes the upstream scenarios come out as their author expected, but the
  real interpreter might behave differently. The checker shows a warning whenever this case
  appears.
- **Input size:** inputs over 60k characters are rejected. Generate one article at a time and
  pass the previous result as `base_spec` to extend it.

## Known packaging issue

`pyproject.toml` has a package-data entry for `agents.text_to_eflint.flint`, but the actual
package is `ontology`. So the ontology files probably wouldn't be included in a built wheel.
