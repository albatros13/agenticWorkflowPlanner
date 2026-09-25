"""CWD-independent loaders for the vendored FLINT ontology, SHACL shapes, and CQ queries.

Replaces upstream ``util/shacl_inference.py`` (a demo with hard-coded relative paths). Assets
are resolved via :mod:`importlib.resources`, so this works whether the project runs from source
or is installed as a wheel/zip.

Import cost is kept low: ``rdflib`` is imported at module load (cheap, and always needed), but
``pyshacl`` is imported lazily inside :func:`validate` so that merely loading graphs or reading
queries does not pull in the heavier validation stack.
"""
from __future__ import annotations

from importlib.resources import files
from typing import TYPE_CHECKING

from rdflib import Graph, Namespace

if TYPE_CHECKING:  # avoid importing pyshacl at module load just for the type hint
    from rdflib.graph import Graph as _Graph

# Canonical FLINT namespace (baseURI from flint.ttl). Bind this on graphs you build so
# serialized output uses the ``flint:`` prefix.
FLINT_NS = Namespace("http://ontology.tno.nl/normengineering/flint#")

_DATA = files(__package__) / "data"
_SHACL = _DATA / "shacl"
_QUERIES = _DATA / "queries"


def _read_version() -> str:
    version_file = files(__package__) / "VERSION"
    try:
        # VERSION's first line is the human-readable tag; later lines carry provenance.
        return version_file.read_text(encoding="utf-8").splitlines()[0].strip()
    except FileNotFoundError:  # pragma: no cover - VERSION is shipped with the package
        return "unknown"


__flint_version__ = _read_version()


def load_ontology() -> Graph:
    """FLINT core ontology + default functions, merged into one graph.

    Use as the ``ont_graph`` for pySHACL (supplies extra knowledge, e.g. default functions,
    for inference) or standalone for reasoning over the schema.
    """
    g = Graph()
    g.bind("flint", FLINT_NS)
    g.parse(_DATA / "flint.ttl", format="turtle")
    g.parse(_DATA / "functions.ttl", format="turtle")
    return g


def load_shapes(*, warnings_only: bool = False) -> Graph:
    """SHACL graph for validating well-formed FLINT knowledge graphs.

    ``flint.ttl`` is included because SHACL rules live inside the ontology itself (see upstream
    ``util/shacl_inference.py``). Pass ``warnings_only=True`` to load the lighter
    ``flint-warnings.ttl`` profile instead of ``full-flint.ttl``.
    """
    g = Graph()
    g.bind("flint", FLINT_NS)
    g.parse(_DATA / "flint.ttl", format="turtle")
    shape_file = "flint-warnings.ttl" if warnings_only else "full-flint.ttl"
    g.parse(_SHACL / shape_file, format="turtle")
    return g


def list_queries() -> list[str]:
    """Names (without ``.rq``) of the bundled SPARQL competency questions."""
    return sorted(p.name[:-3] for p in _QUERIES.iterdir() if p.name.endswith(".rq"))


def load_query(name: str) -> str:
    """Return the text of a bundled competency-question query.

    ``name`` may be given with or without the ``.rq`` suffix, e.g. ``"cq-act"`` or ``"cq-act.rq"``.
    """
    filename = name if name.endswith(".rq") else f"{name}.rq"
    resource = _QUERIES / filename
    try:
        return resource.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(
            f"No competency question named {name!r}. Available: {', '.join(list_queries())}"
        ) from None


def validate(
    data_graph: Graph,
    *,
    inplace: bool = False,
    warnings_only: bool = False,
):
    """Run pySHACL (with SHACL-AF rules + OWL-RL inference) against the FLINT shapes.

    Returns the pySHACL ``(conforms, results_graph, results_text)`` tuple. With
    ``inplace=True`` the inferred triples are written back into ``data_graph``.
    """
    import pyshacl  # lazy: keep graph/query loading free of the validation stack

    return pyshacl.validate(
        data_graph=data_graph,
        shacl_graph=load_shapes(warnings_only=warnings_only),
        ont_graph=load_ontology(),
        advanced=True,          # SHACL-AF (Advanced Features) -> enables SHACL rules
        inference="owlrl",
        inplace=inplace,
    )
