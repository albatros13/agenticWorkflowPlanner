"""eFLINT verifier demo: supply a process, norms and qualification rules, verify, browse past runs.

    uv run uvicorn agents.eflint_verifier.demo.main:app --port 8310     # open http://localhost:8310

Needs the external eFLINT model-checking service (``EFLINT_VERIFIER_URL``, default
``http://127.0.0.1:8100``) for verification; browsing stored runs and inputs works without it.

* ``GET  /api/health``                — demo status + whether the external service is reachable
* ``POST /api/verify``                — run the ``verify_eflint`` intent (payload = ``VerifyRequest``)
* ``GET  /api/runs`` / ``/api/runs/{id}`` — recorded verifications (``storage/runs/<id>/``)
* ``GET  /api/sources/nets[/{ref}]``  — Petri nets saved by ``petri_net_generator`` (PNML)
* ``GET  /api/sources/eflint[/{id}]`` — specifications saved by ``text_to_eflint``
* ``GET  /api/sample``                — a small worked example (process, norms, rules, properties)
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agents.contract import AgentRequest

from ..agent import VERIFY_EFLINT, EflintVerifierHandler
from ..config import VerifierSettings

STATIC_DIR = Path(__file__).resolve().parent / "static"
_REF = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

app = FastAPI(title="eFLINT verifier demo")

SAMPLE = {
    "description": "Two-step process (begin → finish); finishing must record the case. "
                   "The second property is deliberately violated to show a counterexample.",
    "physical": "place ready = 1\nplace active\nplace done\n"
                "transition begin: ready -> active\ntransition finish: active -> done\n",
    "normative": 'Fact recorded Identified by String.\n'
                 'Event register Holds when True Creates recorded("case").\n',
    "qualification": "Sequence\n    begin, finish\nFluents\n    done = 1\nNormative Fluents\nQualify as\n    register.\n",
    "analysis": 'Property completion-recorded Where\n'
                '    Globally ((Tokens done = 1) Implies (Holds recorded("case"))).\n\n'
                "Property never-finish Where Globally (Not (Tokens done = 1)).\n",
    "max_states": 2,
}


def _handler() -> EflintVerifierHandler:
    """The verifier agent (tests override this to inject a fake service / runs folder)."""
    return EflintVerifierHandler()


def _check_ref(ref: str) -> str:
    if not _REF.match(ref or ""):
        raise HTTPException(400, "invalid reference")
    return ref


@app.get("/api/health")
def health() -> dict:
    url = VerifierSettings.from_env().base_url
    reachable, detail = False, ""
    try:
        import httpx  # noqa: PLC0415 - optional dependency of the agent

        httpx.get(url.rstrip("/") + "/v1/jobs/__probe__", timeout=1.5)
        reachable = True  # any HTTP answer (even 404) means the service is up
    except Exception as exc:  # noqa: BLE001 - report, don't fail the page
        detail = f"{type(exc).__name__}: {exc}"
    return {"status": "ok", "service_url": url, "service_reachable": reachable, "detail": detail}


@app.post("/api/verify")
def verify(payload: dict) -> dict:
    """Always 200 with ``{ok, error, output, run_id}`` so the UI can show partial context on failure."""
    result = _handler().handle(AgentRequest(intent=VERIFY_EFLINT, payload=payload,
                                            correlation_id=uuid.uuid4().hex[:12]))
    return {"ok": result.ok, "error": result.error, "output": result.output, "run_id": result.artifacts_ref}


@app.get("/api/runs")
def runs() -> dict:
    rows = _handler().list_runs()
    return {"runs": rows, "count": len(rows)}


@app.get("/api/runs/{run_id}")
def run(run_id: str) -> dict:
    try:
        return _handler().load_run(run_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0]))


# --- inputs from other agents' storage -------------------------------------------------------
def _nets_dir() -> Path:
    from agents.petri_net_generator.agent import PetriNetGeneratorHandler  # noqa: PLC0415

    return PetriNetGeneratorHandler()._runs()


def _eflint_store():
    from agents.text_to_eflint.agent import TextToEflintHandler  # noqa: PLC0415

    return TextToEflintHandler()


@app.get("/api/sources/nets")
def source_nets() -> dict:
    rows = []
    root = _nets_dir()
    if root.is_dir():
        for run_dir in root.iterdir():
            pnml = run_dir / "net.pnml"
            if not pnml.is_file():
                continue
            name = ""
            resp = run_dir / "response.json"
            if resp.is_file():
                try:
                    name = json.loads(resp.read_text(encoding="utf-8")).get("process_name", "")
                except ValueError:
                    pass
            rows.append({"ref": run_dir.name, "name": name or run_dir.name, "modified": pnml.stat().st_mtime})
    rows.sort(key=lambda r: r["modified"], reverse=True)
    return {"nets": rows}


@app.get("/api/sources/nets/{ref}")
def source_net(ref: str) -> dict:
    path = _nets_dir() / _check_ref(ref) / "net.pnml"
    if not path.is_file():
        raise HTTPException(404, f"no saved net {ref!r}")
    return {"ref": ref, "format": "pnml", "source": path.read_text(encoding="utf-8")}


@app.get("/api/sources/eflint")
def source_specs() -> dict:
    return {"specs": [{"run_id": r["run_id"], "title": r["title"], "status": r["status"],
                       "created_at": r["created_at"]} for r in _eflint_store().list_runs()]}


@app.get("/api/sources/eflint/{run_id}")
def source_spec(run_id: str) -> dict:
    try:
        run_data = _eflint_store().load_run(_check_ref(run_id))
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0]))
    files = run_data.get("files", {})
    if "spec.eflint" not in files:
        raise HTTPException(404, f"run {run_id!r} has no self-contained spec.eflint")
    # spec.eflint is self-contained (no #require); scenarios are not part of the normative input.
    return {"run_id": run_id, "title": (run_data.get("spec") or {}).get("title", ""),
            "source": files["spec.eflint"]}


@app.get("/api/sample")
def sample() -> dict:
    return SAMPLE


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=str(STATIC_DIR)), name="static")
