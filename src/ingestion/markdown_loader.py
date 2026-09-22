"""Structure-preserving markdown loader.

Parses headings, paragraphs, list items, block quotes and pipe-tables into
:class:`Block` objects, tracking the heading path and source line range for each
block. Deliberately dependency-free (no markdown library) so it runs anywhere and
gives us exact line locations.
"""
from __future__ import annotations

from pathlib import Path

from .base import Block, BlockType, Document, DocumentLoader, Location


class MarkdownLoader(DocumentLoader):
    suffixes = {".md", ".markdown", ".txt"}

    def can_load(self, path: Path) -> bool:
        return path.suffix.lower() in self.suffixes

    def load(self, path: Path) -> Document:
        text = Path(path).read_text(encoding="utf-8")
        return self.load_text(text, source_document=path.name, doc_id=path.stem)

    def load_text(self, text: str, *, source_document: str, doc_id: str) -> Document:
        lines = text.splitlines()
        blocks: list[Block] = []
        heading_stack: list[tuple[int, str]] = []  # (level, title)
        counter = 0

        def section_path() -> str:
            return " > ".join(title for _, title in heading_stack)

        def new_id() -> str:
            nonlocal counter
            counter += 1
            return f"{doc_id}-b{counter}"

        i = 0
        n = len(lines)
        para_buf: list[str] = []
        para_start = 0

        def flush_paragraph(end_line: int) -> None:
            nonlocal para_buf, para_start
            if para_buf:
                blocks.append(
                    Block(
                        id=new_id(),
                        type=BlockType.PARAGRAPH,
                        text=" ".join(s.strip() for s in para_buf).strip(),
                        location=Location(
                            section=section_path(),
                            line_start=para_start + 1,
                            line_end=end_line,
                        ),
                    )
                )
                para_buf = []

        while i < n:
            line = lines[i]
            stripped = line.strip()

            if not stripped:
                flush_paragraph(i)
                i += 1
                continue

            # Heading
            if stripped.startswith("#"):
                flush_paragraph(i)
                level = len(stripped) - len(stripped.lstrip("#"))
                title = stripped[level:].strip()
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, title))
                blocks.append(
                    Block(
                        id=new_id(),
                        type=BlockType.HEADING,
                        text=title,
                        level=level,
                        location=Location(section=section_path(), line_start=i + 1, line_end=i + 1),
                    )
                )
                i += 1
                continue

            # Table (consecutive pipe rows) — kept whole; tables carry thresholds.
            if "|" in stripped and stripped.count("|") >= 2:
                flush_paragraph(i)
                start = i
                table_lines = []
                while i < n and "|" in lines[i] and lines[i].strip():
                    table_lines.append(lines[i].rstrip())
                    i += 1
                blocks.append(
                    Block(
                        id=new_id(),
                        type=BlockType.TABLE,
                        text="\n".join(table_lines),
                        location=Location(section=section_path(), line_start=start + 1, line_end=i),
                    )
                )
                continue

            # List item (-, *, +, or ordered "1.")
            is_bullet = stripped[:2] in ("- ", "* ", "+ ")
            is_ordered = stripped[0].isdigit() and "." in stripped.split(" ", 1)[0]
            if is_bullet or is_ordered:
                flush_paragraph(i)
                indent = len(line) - len(line.lstrip())
                content = stripped
                if is_bullet:
                    content = stripped[2:].strip()
                elif is_ordered:
                    content = stripped.split(".", 1)[1].strip()
                blocks.append(
                    Block(
                        id=new_id(),
                        type=BlockType.LIST_ITEM,
                        text=content,
                        level=indent // 2,
                        location=Location(section=section_path(), line_start=i + 1, line_end=i + 1),
                    )
                )
                i += 1
                continue

            # Block quote
            if stripped.startswith(">"):
                flush_paragraph(i)
                blocks.append(
                    Block(
                        id=new_id(),
                        type=BlockType.QUOTE,
                        text=stripped.lstrip("> ").strip(),
                        location=Location(section=section_path(), line_start=i + 1, line_end=i + 1),
                    )
                )
                i += 1
                continue

            # Horizontal rule -> ignore
            if set(stripped) <= {"-", "*", "_"} and len(stripped) >= 3:
                i += 1
                continue

            # Paragraph text
            if not para_buf:
                para_start = i
            para_buf.append(line)
            i += 1

        flush_paragraph(n)
        return Document(id=doc_id, source_document=source_document, text=text, blocks=blocks)
