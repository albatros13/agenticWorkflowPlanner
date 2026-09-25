"""Empty FLINT frame templates — the FLINT JSON contract.

Ported from FlintFiller-SRL's ``labels_to_frames.py`` (TNO, Apache-2.0:
``create_empty_flint_format`` / ``create_empty_act_frame`` /
``create_empty_fact_frame`` / ``write_flint_frames_to_json``). The BERT
``##``-token merging helpers from that module are intentionally dropped — the LLM
returns role lists directly, so no per-token reconstruction is needed (see
``roles_to_frame``).
"""
from __future__ import annotations

import json
from pathlib import Path


def create_empty_flint_format() -> dict:
    return {"acts": [], "facts": [], "duties": []}


def create_empty_act_frame() -> dict:
    return {
        "act": "",
        "actor": "",
        "action": "",
        "object": "",
        "recipient": "",
        "preconditions": {"expression": "LITERAL", "operand": True},
        "create": [],
        "terminate": [],
        "sources": [],  # each with validFrom, validTo, juriconnect citation and text
        "explanation": "",
    }


def create_empty_fact_frame() -> dict:
    return {
        "fact": "",
        "function": [],
        "sources": [],  # each with validFrom, validTo, juriconnect citation and text
        "explanation": "",
    }


def write_flint_frames_to_json(flint_frames: dict, flint_file: str | Path) -> None:
    Path(flint_file).write_text(json.dumps(flint_frames), encoding="utf-8")
