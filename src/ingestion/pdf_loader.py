"""PDF loader behind the same :class:`DocumentLoader` abstraction.

``pypdf`` is imported lazily. When it is not installed the loader raises a clear,
actionable error rather than failing at import time — keeping the core package
installable without PDF dependencies. Page numbers are preserved as
``Location.page`` so evidence remains traceable (Step 2).
"""
from __future__ import annotations

from pathlib import Path

from .base import Block, BlockType, Document, DocumentLoader, Location


class PdfLoader(DocumentLoader):
    def can_load(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def load(self, path: Path) -> Document:
        try:
            from pypdf import PdfReader  # noqa: PLC0415 - lazy/optional
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError(
                "Reading PDF input requires 'pypdf' (`pip install pypdf`). "
                "Alternatively, convert the document to markdown/text first."
            ) from exc

        reader = PdfReader(str(path))
        blocks: list[Block] = []
        full_text_parts: list[str] = []
        for page_no, page in enumerate(reader.pages, start=1):
            page_text = page.extract_text() or ""
            full_text_parts.append(page_text)
            for para in (p.strip() for p in page_text.split("\n\n")):
                if not para:
                    continue
                blocks.append(
                    Block(
                        id=f"{path.stem}-p{page_no}-b{len(blocks) + 1}",
                        type=BlockType.PARAGRAPH,
                        text=para,
                        location=Location(section="", page=page_no),
                    )
                )
        return Document(
            id=path.stem,
            source_document=path.name,
            text="\n\n".join(full_text_parts),
            blocks=blocks,
        )
