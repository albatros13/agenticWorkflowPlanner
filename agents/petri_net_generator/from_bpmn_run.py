"""Convert the BPMN model of a ``text_to_bpmn`` storage run into a workflow Petri net.

    uv run python -m agents.petri_net_generator.from_bpmn_run                 # list runs
    uv run python -m agents.petri_net_generator.from_bpmn_run 22b4425994a4    # convert one run
    uv run python -m agents.petri_net_generator.from_bpmn_run --all --out nets/ --dot

A run is given by its id (looked up in the text_to_bpmn runs folder: ``RUNS_DIR``, else
``agents/text_to_bpmn/storage/runs``) or by a path to a run folder. The BPMN XML is read from the
run's ``bpmn.json`` artifact (``--source bpmn``, default) or the graph IR from ``ir.json``
(``--source ir``). Conversion goes through :class:`PetriNetGeneratorHandler`, the same path the
coordinator uses, so each net is validated, soundness-checked and saved in the Petri net agent's
own storage (``runs/<pnml_ref>/``; ``PETRI_NET_RUNS_DIR`` overrides). ``--out DIR`` also writes
``<run_id>.pnml`` (and ``<run_id>.dot`` with ``--dot``) there.

This is a developer tool: it reads another agent's private run folder, which the agents
themselves never do (at runtime the coordinator hands models over through ``storage/projects``).

Exit status: 0 when every run converted, 1 when any failed or needs clarification, 2 for usage
errors (unknown run, missing artifact).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from agents.contract import AgentRequest
from agents.storage import agent_storage

from .agent import GENERATE_PETRI_NET, PetriNetGeneratorHandler

_ARTIFACT = {"bpmn": "bpmn", "ir": "ir"}
_INPUT_TYPE = {"bpmn": "bpmn_xml", "ir": "text_to_bpmn_ir"}


def default_runs_dir() -> Path:
    return Path(os.environ["RUNS_DIR"]) if os.getenv("RUNS_DIR") else agent_storage("text_to_bpmn").path("runs")


def _artifact(run: Path, name: str):
    path = run / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("data")


def resolve_run(ref: str, runs_dir: Path) -> Path:
    path = Path(ref)
    run = path if path.is_dir() else runs_dir / ref
    if not run.is_dir():
        raise FileNotFoundError(f"no text_to_bpmn run {ref!r} (looked in {runs_dir})")
    return run


def list_runs(runs_dir: Path) -> list[dict]:
    if not runs_dir.is_dir():
        return []
    rows = []
    for run in sorted(runs_dir.iterdir()):
        bpmn = run / "bpmn.json"
        if not bpmn.exists():
            continue
        saved = json.loads(bpmn.read_text(encoding="utf-8")).get("saved_at", "")
        ir = _artifact(run, "ir") or {}
        rows.append({"run_id": run.name, "saved_at": saved[:19], "process": ir.get("name", ""),
                     "ir": (run / "ir.json").exists()})
    return sorted(rows, key=lambda r: r["saved_at"])


def convert_run(run: Path, *, source: str = "bpmn", handler: PetriNetGeneratorHandler | None = None,
                dot: bool = False, check_soundness: bool = True) -> dict:
    """Convert one run; returns the agent result as a dict (``status``, ``output``, ``error``)."""
    data = _artifact(run, _ARTIFACT[source])
    if not data:
        raise FileNotFoundError(f"run {run.name!r} has no {_ARTIFACT[source]}.json artifact")
    payload = {"input_type": _INPUT_TYPE[source], _INPUT_TYPE[source]: data,
               "output_formats": ["pnml", "dot"] if dot else ["pnml"], "check_soundness": check_soundness}
    name = (_artifact(run, "ir") or {}).get("name")
    if name:
        payload["model_name"] = name
    result = (handler or PetriNetGeneratorHandler()).handle(
        AgentRequest(intent=GENERATE_PETRI_NET, correlation_id=f"from-run-{run.name}", payload=payload))
    return result.model_dump(mode="json")


def _describe(run_id: str, result: dict) -> str:
    out = result.get("output") or {}
    status = out.get("status") or result["status"]
    if status == "succeeded":
        n, v = out.get("net_summary") or {}, out.get("validation") or {}
        line = (f"{run_id}: OK  {n.get('places')} places, {n.get('transitions')} transitions "
                f"({n.get('silent_transitions')} silent), soundness {v.get('soundness')}")
        for w in out.get("warnings", []):
            line += f"\n    warning {w.get('code')}: {w.get('message')}"
        return line
    if status == "needs_clarification":
        qs = "; ".join(a.get("question", "") for a in out.get("ambiguities", []))
        return f"{run_id}: NEEDS CLARIFICATION  {qs}"
    return f"{run_id}: FAILED  {result.get('error')}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m agents.petri_net_generator.from_bpmn_run",
                                description="Convert text_to_bpmn storage runs into workflow Petri nets (PNML).")
    p.add_argument("runs", nargs="*", help="run ids or run folder paths; none = list the available runs")
    p.add_argument("--all", action="store_true", help="convert every run that has a BPMN model")
    p.add_argument("--source", choices=("bpmn", "ir"), default="bpmn",
                   help="convert the BPMN XML (bpmn.json, default) or the graph IR (ir.json)")
    p.add_argument("--out", type=Path, help="also write <run_id>.pnml (and .dot) into this folder")
    p.add_argument("--dot", action="store_true", help="also produce a Graphviz DOT preview")
    p.add_argument("--no-soundness", action="store_true", help="skip the bounded soundness check")
    p.add_argument("--runs-dir", type=Path, default=None, help="text_to_bpmn runs folder (default: RUNS_DIR or agent storage)")
    args = p.parse_args(argv)
    runs_dir = args.runs_dir or default_runs_dir()

    if not args.runs and not args.all:
        rows = list_runs(runs_dir)
        if not rows:
            print(f"No text_to_bpmn runs with a BPMN model in {runs_dir}.")
            return 0
        print(f"text_to_bpmn runs in {runs_dir}:")
        for r in rows:
            print(f"  {r['run_id']}  {r['saved_at']}  {r['process']}")
        print("Convert one with: python -m agents.petri_net_generator.from_bpmn_run <run_id>  (or --all)")
        return 0

    try:
        runs = ([runs_dir / r["run_id"] for r in list_runs(runs_dir)] if args.all
                else [resolve_run(ref, runs_dir) for ref in args.runs])
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    handler = PetriNetGeneratorHandler()
    failures = 0
    for run in runs:
        try:
            result = convert_run(run, source=args.source, handler=handler, dot=args.dot,
                                 check_soundness=not args.no_soundness)
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(_describe(run.name, result))
        out = result.get("output") or {}
        if out.get("status") != "succeeded":
            failures += 1
            continue
        if out.get("pnml_ref"):
            print(f"    saved: {handler._runs() / out['pnml_ref']}/net.pnml")
        if args.out:
            args.out.mkdir(parents=True, exist_ok=True)
            pnml = out.get("pnml")
            if pnml is None and out.get("pnml_ref"):  # too large to inline: copy the saved file
                pnml = (handler._runs() / out["pnml_ref"] / "net.pnml").read_text(encoding="utf-8")
            if pnml:
                (args.out / f"{run.name}.pnml").write_text(pnml, encoding="utf-8")
                print(f"    wrote: {args.out / (run.name + '.pnml')}")
            if out.get("dot"):
                (args.out / f"{run.name}.dot").write_text(out["dot"], encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
