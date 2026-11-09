"""The document library: upload (register) and find regulatory documents.

A document is stored under ``<root>/<doc_id>/`` as two files:

* ``document.json`` — the parsed :class:`Document` (structure + exact source locations),
  reusing the text_to_bpmn ingestion loaders so markdown/txt/PDF are all supported;
* ``meta.json`` — a :class:`DocumentRecord` the UI lists and selects from, including which
  vectorizations have been run.

``doc_id`` is a content hash, so re-uploading the same bytes is idempotent (returns the existing
record) rather than creating duplicates. Only metadata/structure is kept here — vectors live in
Qdrant (see :mod:`.vectorizer`).
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from agents.storage import agent_storage

from agents.text_to_bpmn.pipeline.ingestion.base import Document, DocumentLoader
from agents.text_to_bpmn.pipeline.ingestion.markdown_loader import MarkdownLoader
from agents.text_to_bpmn.pipeline.ingestion.pdf_loader import PdfLoader

_PREVIEW_CHARS = 240
_DOC_ID = re.compile(r"^[0-9a-f]{12}$")  # see _doc_id: a 12-hex-digit content hash


class DocumentRecord(BaseModel):
    """Catalogue entry for one stored document (``meta.json``)."""

    doc_id: str
    title: str
    source_document: str
    kind: str                       # "markdown" | "pdf" | "text"
    n_blocks: int = 0
    n_chars: int = 0
    preview: str = ""
    created_at: str = ""
    vectorized: bool = False
    vectorizations: list[dict] = Field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DocumentLibrary:
    """File-backed catalogue of regulatory documents under ``root``."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._loaders: list[DocumentLoader] = [MarkdownLoader(), PdfLoader()]

    # ---- upload --------------------------------------------------------------
    def add_from_text(
        self, text: str, *, title: str | None = None, source_document: str | None = None
    ) -> DocumentRecord:
        text = text or ""
        if not text.strip():
            raise ValueError("Cannot upload an empty document.")
        doc_id = self._doc_id(text.encode("utf-8"))
        source = source_document or (title or f"{doc_id}.md")
        document = MarkdownLoader().load_text(text, source_document=source, doc_id=doc_id)
        return self._store(document, kind="text", title=title or source)

    def add_from_path(self, path: str | Path, *, name: str | None = None) -> DocumentRecord:
        """Register a file. ``name`` is the user-facing file name (title + source) — needed when
        ``path`` is a temporary copy of an upload whose real name would otherwise be lost."""
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"No such file: {path}")
        loader = self._loader_for(path)
        if loader is None:
            raise ValueError(f"Unsupported document type: {path.suffix!r}")
        doc_id = self._doc_id(path.read_bytes())
        document = loader.load(path)
        document.id = doc_id  # normalize to the content hash for idempotent storage
        for block in document.blocks:
            block.id = block.id.replace(path.stem, doc_id, 1)
        if name:
            document.source_document = name
        kind = "pdf" if path.suffix.lower() == ".pdf" else "markdown"
        return self._store(document, kind=kind, title=name or path.name)

    # ---- find / read ---------------------------------------------------------
    def ids(self) -> set[str]:
        """Ids of the stored documents, from the folder names alone (no metadata parsing)."""
        if not self.root.is_dir():
            return set()
        return {d.name for d in self.root.iterdir() if _DOC_ID.match(d.name) and (d / "meta.json").is_file()}

    def find(self, query: str | None = None) -> list[DocumentRecord]:
        records = [self._read_meta(d) for d in sorted(self.root.iterdir()) if d.is_dir()]
        records = [r for r in records if r is not None]
        if query:
            q = query.lower()
            records = [r for r in records if self._matches(r, q)]
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    def _doc_dir(self, doc_id: str) -> Path:
        """The folder of ``doc_id``; an id that is not a content hash cannot exist (KeyError)."""
        if not isinstance(doc_id, str) or not _DOC_ID.match(doc_id):
            raise KeyError(f"No document with id {doc_id!r}")
        return self.root / doc_id

    def get(self, doc_id: str) -> DocumentRecord:
        meta = self._read_meta(self._doc_dir(doc_id))
        if meta is None:
            raise KeyError(f"No document with id {doc_id!r}")
        return meta

    def load_document(self, doc_id: str) -> Document:
        path = self._doc_dir(doc_id) / "document.json"
        if not path.is_file():
            raise KeyError(f"No document with id {doc_id!r}")
        return Document.model_validate_json(path.read_text(encoding="utf-8"))

    def record_vectorization(self, doc_id: str, info: dict) -> DocumentRecord:
        meta = self.get(doc_id)
        entry = {**info, "at": _now_iso()}
        meta.vectorizations = [v for v in meta.vectorizations if v.get("collection") != info.get("collection")]
        meta.vectorizations.append(entry)
        meta.vectorized = True
        self._write_meta(meta)
        return meta

    # ---- internals -----------------------------------------------------------
    def _store(self, document: Document, *, kind: str, title: str) -> DocumentRecord:
        doc_dir = self.root / document.id
        existing = self._read_meta(doc_dir)
        if existing is not None:
            return existing  # idempotent: same content hash already uploaded
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "document.json").write_text(
            document.model_dump_json(indent=2), encoding="utf-8"
        )
        record = DocumentRecord(
            doc_id=document.id,
            title=title,
            source_document=document.source_document,
            kind=kind,
            n_blocks=len(document.blocks),
            n_chars=len(document.text),
            preview=document.text[:_PREVIEW_CHARS].strip(),
            created_at=_now_iso(),
        )
        self._write_meta(record)
        return record

    def _write_meta(self, record: DocumentRecord) -> None:
        (self.root / record.doc_id / "meta.json").write_text(
            record.model_dump_json(indent=2), encoding="utf-8"
        )

    def _read_meta(self, doc_dir: Path) -> DocumentRecord | None:
        meta_path = doc_dir / "meta.json"
        if not meta_path.is_file():
            return None
        return DocumentRecord.model_validate_json(meta_path.read_text(encoding="utf-8"))

    def _loader_for(self, path: Path) -> DocumentLoader | None:
        return next((ldr for ldr in self._loaders if ldr.can_load(path)), None)

    @staticmethod
    def _matches(record: DocumentRecord, q: str) -> bool:
        haystack = " ".join([record.title, record.source_document, record.preview, record.doc_id])
        return q in haystack.lower()

    @staticmethod
    def _doc_id(data: bytes) -> str:
        return hashlib.sha1(data).hexdigest()[:12]


def default_library_root() -> Path:
    """The regulatory document library: ``REGDOCS_DIR`` if set, else the agent's private storage.
    The one definition shared by the regulatory_docs agent and the agents that read its documents."""
    env = os.getenv("REGDOCS_DIR")
    return Path(env) if env else agent_storage("regulatory_docs").path("documents")
