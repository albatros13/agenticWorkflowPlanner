"""Semantic chunking.

Chunks by *section* rather than by raw token count (guideline Step 2: "chunk
semantically rather than only by token count"). Each chunk keeps the ids and line
range of the blocks it came from so retrieval can return the original text and
exact location. Oversized sections are split on block boundaries, never mid-block.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .base import Block, BlockType, Document, Location


class Chunk(BaseModel):
    id: str
    text: str
    section: str
    block_ids: list[str] = Field(default_factory=list)
    location: Location = Field(default_factory=Location)


class SemanticChunker:
    def __init__(self, max_chars: int = 1200):
        self.max_chars = max_chars

    def chunk(self, document: Document) -> list[Chunk]:
        # Group blocks by their section path, preserving order.
        groups: list[tuple[str, list[Block]]] = []
        for block in document.blocks:
            if block.type == BlockType.HEADING:
                # A heading opens a new group for its own section.
                groups.append((block.location.section, [block]))
                continue
            if groups and groups[-1][0] == block.location.section:
                groups[-1][1].append(block)
            else:
                groups.append((block.location.section, [block]))

        chunks: list[Chunk] = []
        for section, blocks in groups:
            for part in self._split(blocks):
                if not part:
                    continue
                text = "\n".join(b.text for b in part).strip()
                if not text:
                    continue
                starts = [b.location.line_start for b in part if b.location.line_start]
                ends = [b.location.line_end for b in part if b.location.line_end]
                pages = [b.location.page for b in part if b.location.page]
                chunks.append(
                    Chunk(
                        id=f"{document.id}-c{len(chunks) + 1}",
                        text=text,
                        section=section,
                        block_ids=[b.id for b in part],
                        location=Location(
                            section=section,
                            line_start=min(starts) if starts else None,
                            line_end=max(ends) if ends else None,
                            page=pages[0] if pages else None,
                        ),
                    )
                )
        return chunks

    def _split(self, blocks: list[Block]) -> list[list[Block]]:
        """Split a section's blocks so no chunk exceeds ``max_chars`` (block-aligned)."""
        parts: list[list[Block]] = []
        current: list[Block] = []
        size = 0
        for b in blocks:
            blen = len(b.text) + 1
            if current and size + blen > self.max_chars:
                parts.append(current)
                current, size = [], 0
            current.append(b)
            size += blen
        if current:
            parts.append(current)
        return parts
