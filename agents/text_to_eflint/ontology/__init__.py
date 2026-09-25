"""Vendored FLINT ontology assets + a thin rdflib/pySHACL loader.

The ``.ttl`` / ``.rq`` files under ``data/`` are a pinned copy of the upstream FLINT ontology
(https://gitlab.com/normativesystems/knowledge-modeling/flint-ontology, tag ``v1.0.0`` — see
``VERSION``). They are data, not code: this package only adds a small, CWD-independent loader
so the rest of the agent can build ontology/SHACL graphs without knowing file layout.

Licensing (preserved from upstream): everything is Apache-2.0 EXCEPT the SHACL profiles under
``data/shacl/`` which are MPL-2.0 (see ``data/shacl/LICENSE``). MPL is file-level copyleft —
keep that license file next to the shapes and disclose changes to those specific files.
"""

from agents.text_to_eflint.ontology.loader import (
    load_ontology,
    load_shapes,
    load_query,
    list_queries,
    validate,
    FLINT_NS,
    __flint_version__,
)

__all__ = [
    "load_ontology",
    "load_shapes",
    "load_query",
    "list_queries",
    "validate",
    "FLINT_NS",
    "__flint_version__",
]
