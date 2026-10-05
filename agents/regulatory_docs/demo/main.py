"""Demo API: upload/find regulatory documents, then vectorize a selected one into Qdrant.

Thin HTTP shim over :class:`~agents.regulatory_docs.agent.RegulatoryDocsHandler`:

* ``POST /api/upload``      — register a document from pasted text (``{text, title}``)
* ``POST /api/upload_file`` — register a document from an uploaded file (markdown/txt/PDF)
* ``GET  /api/documents``   — list/search uploaded documents (``?query=``)
* ``GET  /api/profiles``    — vectorization options from ``config/vectorization.json``
* ``POST /api/vectorize``   — vectorize a selected document (``{doc_id, profile}``)

The static frontend at ``/`` lets you paste/upload, pick a document, choose a profile, and push
it to Qdrant. Mirrors the text->BPMN / text->eFLINT demos. New clients should talk to the
coordinator instead.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agents.contract import AgentRequest
from agents.regulatory_docs.agent import (
    FIND_DOCUMENTS,
    UPLOAD_DOCUMENT,
    VECTORIZE_DOCUMENT,
    RegulatoryDocsHandler,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Regulatory docs — upload / find / vectorize")


def _handler() -> RegulatoryDocsHandler:
    """Build the agent handler (tests override this to inject offline embedder/store)."""
    return RegulatoryDocsHandler()


def _run(intent: str, payload: dict) -> dict:
    result = _handler().handle(AgentRequest(intent=intent, payload=payload))
    if not result.ok:
        raise HTTPException(status_code=422, detail=result.error)
    return result.output


class UploadRequest(BaseModel):
    text: str = Field(min_length=1)
    title: str = ""


class VectorizeRequest(BaseModel):
    doc_id: str = Field(min_length=1)
    profile: str = ""


@app.post("/api/upload")
def upload(req: UploadRequest) -> dict:
    return _run(UPLOAD_DOCUMENT, {"text": req.text, "title": req.title or None})


@app.post("/api/upload_file")
def upload_file(file: UploadFile = File(...)) -> dict:
    suffix = Path(file.filename or "document.md").suffix or ".md"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file.file.read())
        tmp_path = tmp.name
    try:
        return _run(UPLOAD_DOCUMENT, {"path": tmp_path})
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.get("/api/documents")
def documents(query: str = "") -> dict:
    return _run(FIND_DOCUMENTS, {"query": query or None})


@app.get("/api/profiles")
def profiles() -> dict:
    return {"profiles": _run(FIND_DOCUMENTS, {}).get("profiles", [])}


@app.post("/api/vectorize")
def vectorize(req: VectorizeRequest) -> dict:
    return _run(VECTORIZE_DOCUMENT, {"doc_id": req.doc_id, "profile": req.profile or None})


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(STATIC_DIR)), name="static")
