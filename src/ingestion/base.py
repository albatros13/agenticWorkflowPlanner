"""Structured document model shared by all loaders.

Loaders turn raw input (markdown, PDF, ...) into a :class:`Document` that
preserves structure (headings, blocks, tables) and *location* so that every
downstream element can be traced back to an exact source span (guideline
Step 2: "preserve exact source locations whenever available").
"""
from __future__ import annotations

import abc
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class BlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    QUOTE = "quote"


class Location(BaseModel):
    """Where a block came from. ``page`` is populated by paginated sources."""

    section: str = ""          # heading path, e.g. "Diagnosis > PSA"
    line_start: int | None = None
    line_end: int | None = None
    page: int | None = None


class Block(BaseModel):
    id: str
    type: BlockType
    text: str
    level: int = 0             # heading depth / list nesting
    location: Location = Field(default_factory=Location)


class Document(BaseModel):
    id: str
    source_document: str       # filename or logical name
    text: str                  # full raw text (kept for hashing/audit)
    blocks: list[Block] = Field(default_factory=list)

    def section_paths(self) -> list[str]:
        seen: list[str] = []
        for b in self.blocks:
            if b.location.section and b.location.section not in seen:
                seen.append(b.location.section)
        return seen


class DocumentLoader(abc.ABC):
    """Abstraction over input formats (Step 2: "support PDF/text through an abstraction")."""

    @abc.abstractmethod
    def can_load(self, path: Path) -> bool: ...

    @abc.abstractmethod
    def load(self, path: Path) -> Document: ...
