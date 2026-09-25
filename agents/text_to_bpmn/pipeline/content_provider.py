"""File/image/HTML helpers.

This module exists (in part) to satisfy the import in
``api/anthropic_client.py`` (``from agents.text_to_bpmn.pipeline.content_provider import ...``).
It keeps small, dependency-free helpers for loading local content into LLM calls.
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from .config import REPO_ROOT

_IMAGE_MEDIA_FALLBACK = "image/jpeg"


def get_full_paths(paths: list[str]) -> list[str]:
    """Resolve possibly-relative paths against the repo root; drop missing ones."""
    resolved: list[str] = []
    for p in paths:
        candidate = Path(p)
        if not candidate.is_absolute():
            candidate = REPO_ROOT / candidate
        if candidate.exists():
            resolved.append(str(candidate))
    return resolved


def encode_image_with_type(path: str) -> dict:
    """Return ``{"type": <media_type>, "data": <base64>}`` for an image file."""
    media_type, _ = mimetypes.guess_type(path)
    with open(path, "rb") as fh:
        data = base64.b64encode(fh.read()).decode("utf-8")
    return {"type": media_type or _IMAGE_MEDIA_FALLBACK, "data": data}


def get_html_content(paths: list[str]) -> list[str]:
    """Read HTML (or any text) files, resolving paths as in :func:`get_full_paths`."""
    contents: list[str] = []
    for path in get_full_paths(paths):
        contents.append(Path(path).read_text(encoding="utf-8", errors="replace"))
    return contents
