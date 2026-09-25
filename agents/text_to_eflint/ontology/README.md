# Vendored FLINT ontology

A pinned, in-tree copy of the [FLINT ontology](https://gitlab.com/normativesystems/knowledge-modeling/flint-ontology)
(**tag `v1.0.0`** — see [`VERSION`](./VERSION)) plus a thin loader. FLINT is a Semantic-Web
knowledge model for normative systems, described as Act frames and Fact frames.

These are **data assets** (`.ttl` / `.rq`), not an upstream Python API — the only upstream code
was a demo script with hard-coded paths, which we replaced with [`loader.py`](./loader.py).

## Layout

| Path | What | License |
|---|---|---|
| `data/flint.ttl` | FLINT core ontology (contains SHACL rules) | Apache-2.0 |
| `data/functions.ttl` | Default FLINT functions | Apache-2.0 |
| `data/shacl/full-flint.ttl` | SHACL profile: well-formed FLINT graphs | **MPL-2.0** |
| `data/shacl/flint-warnings.ttl` | SHACL profile: warnings-only | **MPL-2.0** |
| `data/shacl/LICENSE` | MPL-2.0 text for the shapes | — |
| `data/queries/*.rq` | SPARQL competency questions | Apache-2.0 |
| `LICENSE` | Apache-2.0 text | — |

> **Licensing:** everything is Apache-2.0 **except** the SHACL profiles under `data/shacl/`,
> which are MPL-2.0. MPL is file-level copyleft — keep `data/shacl/LICENSE` next to the shapes
> and disclose any changes made to those specific files.

## Usage

```python
from agents.text_to_eflint import ontology

onto = ontology.load_ontology()  # flint.ttl + functions.ttl
shapes = ontology.load_shapes()  # full-flint profile (+ ontology rules)

# Validate / infer over a FLINT knowledge graph:
from rdflib import Graph

data = Graph().parse("some_model.ttl", format="turtle")
conforms, report_graph, report_text = ontology.validate(data, inplace=True)

# Competency questions:
ontology.list_queries()  # -> ['cq-act', 'cq-act-action', ...]
data.query(ontology.load_query("cq-act"))
```

Requires the `eflint` extra: `pip install -e ".[eflint]"` (pulls in `rdflib` + `pyshacl`).

## Updating from upstream

1. `git -C <flint-ontology clone> checkout <new tag>`
2. Re-copy `flint.ttl`, `functions.ttl`, `shacl/*.ttl`, `shacl/LICENSE`, and
   `competency-questions/*.rq` into the matching paths here.
3. Bump [`VERSION`](./VERSION) and re-run the agent's tests.
