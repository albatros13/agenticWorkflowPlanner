"""Turn LLM role output straight into FLINT act frames.

The BERT path in FlintFiller-SRL needed per-token tuples because the model labels
tokens; the LLM returns role *lists* directly, so we build the act frame without the
token-merging code (plan §4.4). This reproduces the original end-to-end behaviour
(``main.py`` -> ``create_frames``): one act frame per sentence, dropping frames with
an empty action.
"""
from __future__ import annotations

from typing import Iterable

from .frame_templates import create_empty_act_frame


def role_dict_to_act_frame(roles: dict) -> dict:
    """Map ``{action, actor, object, recipient, other}`` role lists to an act frame."""
    action = " ".join(roles.get("action", [])).strip()
    obj = " ".join(roles.get("object", [])).strip()

    frame = create_empty_act_frame()
    frame.update(
        {
            "act": (action + " " + obj).strip(),
            "actor": " ".join(roles.get("actor", [])).strip(),
            "action": action,
            "object": obj,
            "recipient": " ".join(roles.get("recipient", [])).strip(),
        }
    )
    return frame


def frames_from_sentences(sentences: Iterable[str], tag_roles) -> dict:
    """Build a full FLINT format from sentences using a ``tag_roles(sentence)->dict`` fn.

    ``tag_roles`` is typically :meth:`FlintFrameLabeller.tag_roles`. Act frames whose
    action came back empty are dropped, matching the original ``create_frames`` rule.
    """
    acts = [role_dict_to_act_frame(tag_roles(s)) for s in sentences]
    acts = [a for a in acts if a["action"]]
    return {"acts": acts, "facts": [], "duties": []}
