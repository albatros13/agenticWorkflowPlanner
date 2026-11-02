# Reference eFLINT specification (vendored)

A pinned copy of the *data governance framework* eFLINT specification and its scenarios from
[Ericmolenaar/Scattered-Directive](https://github.com/Ericmolenaar/Scattered-Directive/tree/thesisEric/data%20governance%20framework),
branch `thesisEric`, commit `7db8668f67eb34074ae5923b8f6e5f5242a91337`. MIT-licensed, see
[`LICENSE`](LICENSE).

It formalises GDPR (Art. 5, 6, 7, 26), the Data Act (Art. 5, 8) and the Electricity Directive
(Art. 23, 24) for a vertical-federated-learning training round across three companies:

| file | content |
|---|---|
| `Ontology.eflint` | atomic types (controller, data-subject, data, purpose, training-round) |
| `Consent.eflint` | consent fact + give/withdraw consent acts |
| `Participation.eflint` | legal-condition facts, the privacy-technique duty, the six-step act chain |
| `Init.eflint` | domain instances + initial state |
| `scenarios/Scenario-{1..5}.eflint` | compliant path, consent withdrawn, privacy technique not applied, unauthorised third party, second training round |

The generator uses these files in two ways:

* **few-shot reference**: parsed into the generator's JSON spec format and shown to the LLM as a
  worked example of the modelling approach (see `generation/reference.py`);
* **golden tests**: the reference simulator must reproduce the expectations the author wrote in
  the scenario comments (`tests/text_to_eflint/test_eflint_reference.py`).

Changes from upstream: the `Scenario's/` folder is renamed `scenarios/`. The `*_lite.eflint`
variants and the 1.5 MB FLINT-editor baseline (`Flint Baseline/*.json`) are not vendored.
