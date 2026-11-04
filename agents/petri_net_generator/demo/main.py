"""Petri net viewer demo: browse, upload and simulate PNML nets in the browser.

    uv run uvicorn agents.petri_net_generator.demo.main:app --port 8210     # open http://localhost:8210

* lists the nets saved by the Petri net generator (``storage/runs/<pnml_ref>/net.pnml``;
  ``PETRI_NET_RUNS_DIR`` overrides) and accepts any uploaded or pasted ``.pnml``;
* converts a saved ``text_to_bpmn`` run into a net on demand (same path as ``from_bpmn_run``);
* draws the net with Graphviz (in the browser via WebAssembly; ``POST /api/svg`` is a server-side
  fallback when the ``dot`` binary is installed) and plays the token game: click an enabled
  transition to fire it (PM4Py semantics).

The API is stateless: the client sends the PNML (or a saved net's ``ref``) and the current
marking with every request.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from ..agent import PetriNetGeneratorHandler
from ..from_bpmn_run import convert_run, default_runs_dir, list_runs
from .view import PnmlError, build_view, fire, parse_pnml

STATIC_DIR = Path(__file__).resolve().parent / "static"
_REF = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

app = FastAPI(title="Petri net viewer")
_handler = PetriNetGeneratorHandler()  # resolves the runs folder (PETRI_NET_RUNS_DIR / agent storage)


def _runs() -> Path:
    return _handler._runs()


def _net_path(ref: str) -> Path:
    if not _REF.match(ref):
        raise HTTPException(400, "invalid net reference")
    path = _runs() / ref / "net.pnml"
    if not path.is_file():
        raise HTTPException(404, f"no saved net {ref!r}")
    return path


class NetSource(BaseModel):
    pnml: str | None = None
    ref: str | None = None
    rankdir: Literal["LR", "TB"] = "LR"

    @model_validator(mode="after")
    def _one_source(self):
        if bool(self.pnml) == bool(self.ref):
            raise ValueError("provide exactly one of 'pnml' or 'ref'")
        return self

    def load(self):
        text = self.pnml if self.pnml else _net_path(self.ref).read_bytes()
        try:
            return parse_pnml(text)
        except PnmlError as exc:
            raise HTTPException(422, str(exc)) from exc

    def name(self) -> str:
        if not self.ref:
            return ""
        resp = _runs() / self.ref / "response.json"
        if resp.is_file():
            return json.loads(resp.read_text(encoding="utf-8")).get("process_name", "")
        return ""


class ViewRequest(NetSource):
    marking: dict[str, int] | None = None
    analyze: bool = True


class FireRequest(NetSource):
    marking: dict[str, int] | None = None
    transition: str = Field(min_length=1)


class ConvertRequest(BaseModel):
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    source: Literal["bpmn", "ir"] = "bpmn"


class SvgRequest(BaseModel):
    dot: str = Field(min_length=1, max_length=2_000_000)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "server_svg": shutil.which("dot") is not None}


@app.get("/api/nets")
def nets() -> dict:
    """Nets saved by the generator, newest first."""
    rows = []
    root = _runs()
    if root.is_dir():
        for run in root.iterdir():
            pnml = run / "net.pnml"
            if not pnml.is_file():
                continue
            row = {"ref": run.name, "name": "", "input_type": "", "soundness": None, "summary": None,
                   "modified": pnml.stat().st_mtime}
            resp = run / "response.json"
            if resp.is_file():
                try:
                    d = json.loads(resp.read_text(encoding="utf-8"))
                    row.update(name=d.get("process_name", ""), input_type=d.get("input_type", ""),
                               soundness=(d.get("validation") or {}).get("soundness"), summary=d.get("net_summary"))
                except ValueError:
                    pass
            rows.append(row)
    rows.sort(key=lambda r: r["modified"], reverse=True)
    return {"runs_dir": str(root), "nets": rows}


@app.get("/api/nets/{ref}/pnml")
def net_pnml(ref: str) -> PlainTextResponse:
    return PlainTextResponse(_net_path(ref).read_text(encoding="utf-8"), media_type="application/xml",
                             headers={"Content-Disposition": f'attachment; filename="{ref}.pnml"'})


@app.post("/api/view")
def view(req: ViewRequest) -> dict:
    parsed = req.load()
    try:
        return build_view(parsed, req.marking, rankdir=req.rankdir, analyze=req.analyze, name=req.name())
    except PnmlError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/fire")
def fire_transition(req: FireRequest) -> dict:
    parsed = req.load()
    try:
        marking = fire(parsed, req.marking, req.transition)
        return build_view(parsed, marking, rankdir=req.rankdir, analyze=False, name=req.name())
    except PnmlError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/bpmn_runs")
def bpmn_runs() -> dict:
    """text_to_bpmn runs that have a BPMN model and can be converted."""
    root = default_runs_dir()
    return {"runs_dir": str(root), "runs": list(reversed(list_runs(root)))}


@app.post("/api/convert")
def convert(req: ConvertRequest) -> dict:
    run = default_runs_dir() / req.run_id
    if not run.is_dir():
        raise HTTPException(404, f"no text_to_bpmn run {req.run_id!r}")
    try:
        result = convert_run(run, source=req.source, handler=_handler)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    out = result.get("output") or {}
    if out.get("status") != "succeeded":
        raise HTTPException(422, result.get("error") or f"conversion {out.get('status')}")
    return {"ref": out["pnml_ref"], "name": out.get("process_name", ""), "warnings": out.get("warnings", [])}


@app.post("/api/svg")
def svg(req: SvgRequest) -> Response:
    """Server-side rendering fallback (needs the Graphviz ``dot`` binary)."""
    if shutil.which("dot") is None:
        raise HTTPException(503, "Graphviz 'dot' is not installed on the server")
    import graphviz  # noqa: PLC0415 - installed with PM4Py

    dot = req.dot
    if "layout=neato" in dot:  # pinned PNML positions; fall back to automatic layout without neato
        try:
            return Response(graphviz.Source(dot, engine="neato").pipe(format="svg", quiet=True),
                            media_type="image/svg+xml")
        except Exception:
            dot = re.sub(r"\s*graph \[layout=neato[^\]]*\];", "\n  graph [rankdir=LR];", dot)
            dot = re.sub(r', pos="[^"]*"', "", dot)
    try:
        data = graphviz.Source(dot, engine="dot").pipe(format="svg", quiet=True)
    except Exception as exc:  # graphviz.ExecutableNotFound / CalledProcessError
        raise HTTPException(422, f"Graphviz failed: {exc}") from exc
    return Response(data, media_type="image/svg+xml")


@app.get("/")
def index() -> FileResponse:
    # no-cache: browsers revalidate (cheap 304) instead of showing a stale page after an update
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
