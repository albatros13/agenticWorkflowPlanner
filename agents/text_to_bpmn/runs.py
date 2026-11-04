"""Read back the pipeline runs saved under the agent's storage (``runs/<run_id>/``).

:func:`load_run` rebuilds the *same payload* the agent returns for a fresh generation
(``bpmn_xml``, ``dmn_xml``, ``resources``, ``validation``, …), so a stored diagram can be shown
exactly like a new one:

* the diagram is the stored ``bpmn.json`` / ``dmn.json`` (the record of what was generated);
* ``resources`` are rebuilt with :func:`~.shaping.build_resources` from the stored evidence, IR
  and terminology. The compiled graph (decision tables, node/flow counts) is not stored, so it is
  rebuilt from the IR, which is deterministic code (no LLM);
* ``validation`` re-runs the schema and connectivity checks on the stored XML.

Runs made by an older pipeline version may not validate against today's models. Then the
diagram is still returned, the affected resources are left empty, and ``warnings`` says why.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from agents.storage import agent_storage

from .pipeline.bpmn.model import build_graph
from .pipeline.evidence.models import EvidenceSet
from .pipeline.ir.models import Process
from .pipeline.terminology.models import TerminologyAnnotation
from .pipeline.validation.schema import check_connectivity, validate_bpmn, validate_dmn
from .shaping import build_resources

_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class RunNotFoundError(LookupError):
    """No stored run with that id, or the run has no BPMN model."""


def runs_dir() -> Path:
    """The agent's runs folder: ``RUNS_DIR`` if set, else ``storage/runs`` (config/storage.json)."""
    return Path(os.environ["RUNS_DIR"]) if os.getenv("RUNS_DIR") else agent_storage("text_to_bpmn").path("runs")


def _artifact(run: Path, name: str) -> dict | None:
    """The full artifact record (``name``, ``saved_at``, ``meta``, ``data``), or None."""
    path = run / f"{name}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _run_path(run_id: str, root: Path | None) -> Path:
    if not _RUN_ID.match(run_id or ""):
        raise RunNotFoundError(f"invalid run id {run_id!r}")
    run = (root or runs_dir()) / run_id
    if not (run / "bpmn.json").is_file():
        raise RunNotFoundError(f"no stored run {run_id!r} with a BPMN model")
    return run


def list_runs(root: Path | None = None) -> list[dict]:
    """Stored runs that have a BPMN model, newest first."""
    root = root or runs_dir()
    if not root.is_dir():
        return []
    rows = []
    for run in root.iterdir():
        bpmn = _artifact(run, "bpmn") if _RUN_ID.match(run.name) else None
        if not bpmn:
            continue
        meta = bpmn.get("meta") or {}
        ir = (_artifact(run, "ir") or {}).get("data") or {}
        doc = (_artifact(run, "document") or {}).get("data") or {}
        rows.append({
            "run_id": run.name,
            "saved_at": bpmn.get("saved_at", ""),
            "process_name": ir.get("name", "") if isinstance(ir, dict) else "",
            "source_document": doc.get("source_document", "") if isinstance(doc, dict) else "",
            "provider": meta.get("provider", ""),
            "nodes": meta.get("n_nodes"),
            "flows": meta.get("n_flows"),
            "has_dmn": (run / "dmn.json").is_file(),
        })
    rows.sort(key=lambda r: r["saved_at"], reverse=True)
    return rows


def load_run(run_id: str, root: Path | None = None) -> dict:
    """The agent's output payload for a stored run, plus ``source_text``, ``saved_at``, ``warnings``."""
    run = _run_path(run_id, root)
    bpmn = _artifact(run, "bpmn")
    bpmn_xml = bpmn.get("data") if bpmn else None
    if not isinstance(bpmn_xml, str) or not bpmn_xml.strip():
        raise RunNotFoundError(f"run {run_id!r} has an empty or unreadable BPMN artifact")
    dmn = _artifact(run, "dmn")
    dmn_xml = dmn.get("data") if dmn and isinstance(dmn.get("data"), str) else None
    warnings: list[str] = []

    def model(name: str, parse):
        rec = _artifact(run, name)
        if rec is None:
            warnings.append(f"{name}.json is missing")
            return None
        try:
            return parse(rec["data"])
        except Exception as exc:  # older/newer schema
            warnings.append(f"{name}.json could not be read with the current pipeline models: "
                            f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
            return None

    evidence = model("evidence", EvidenceSet.model_validate)
    process = model("ir", Process.model_validate)
    annotations = model("terminology", lambda d: [TerminologyAnnotation.model_validate(a) for a in d])

    graph = None
    if process is not None:
        try:
            graph = build_graph(process)
        except Exception as exc:
            warnings.append(f"the process graph could not be rebuilt from ir.json: {type(exc).__name__}: {exc}")

    resources = build_resources(
        evidence or EvidenceSet.model_construct(items=[]),
        process or _EmptyProcess(),
        annotations or [],
        _Model(graph) if graph is not None else _Model(None),
    )

    vb, cc = validate_bpmn(bpmn_xml), check_connectivity(bpmn_xml)
    vd = validate_dmn(dmn_xml) if dmn_xml else None
    doc = (_artifact(run, "document") or {}).get("data") or {}
    return {
        "run_id": run.name,
        "provider": (bpmn.get("meta") or {}).get("provider", ""),
        "bpmn_xml": bpmn_xml,
        "dmn_xml": dmn_xml,
        "resources": resources,
        "validation": {
            "bpmn_valid": vb.ok,
            "bpmn_errors": vb.errors[:5],
            "dmn_valid": vd.ok if vd else None,
            "connectivity_ok": cc.ok,
            "connectivity_errors": cc.errors[:5],
        },
        "stored": True,
        "saved_at": bpmn.get("saved_at", ""),
        "process_name": process.name if process is not None else "",
        "source_text": doc.get("text", "") if isinstance(doc, dict) else "",
        "source_document": doc.get("source_document", "") if isinstance(doc, dict) else "",
        "warnings": warnings,
    }


class _EmptyProcess:
    """Stand-in when ir.json is unreadable: no actors, elements or relations."""
    actors: list = []
    elements: list = []
    relations: list = []
    name = ""


class _Model:
    """``build_resources`` only reads ``model.graph`` (decisions, nodes, flows)."""

    def __init__(self, graph):
        self.graph = graph if graph is not None else _EmptyGraph()


class _EmptyGraph:
    decisions: list = []
    nodes: list = []
    flows: list = []
