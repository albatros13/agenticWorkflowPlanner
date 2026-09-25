"""LLM-based FLINT act-frame labelling (SRL).

Turns a legal sentence into semantic roles (action / actor / object / recipient /
other) and assembles those roles into FLINT act frames. This is a provider-agnostic
port of the *Generative LLM* approach from TNO's FlintFiller-SRL — only the reusable
IP (role schema, gold few-shot examples, frame templates, roles->frame mapping) is
carried over; the OpenAI-specific transport, BERT fine-tuning and evaluation
scaffolding are dropped. See ``todo/text_to_eflint/plan.md`` for the reuse rationale.

The LLM call goes through :class:`agents.text_to_bpmn.pipeline.llm.base.LLMProvider`,
so any provider (Anthropic, OpenAI, a scripted mock, a local model) works unchanged.
"""
from __future__ import annotations

from .frame_templates import (
    create_empty_act_frame,
    create_empty_fact_frame,
    create_empty_flint_format,
    write_flint_frames_to_json,
)
from .labeller import FlintFrameLabeller
from .roles_to_frame import frames_from_sentences, role_dict_to_act_frame
from .schema import FLINT_ROLE_SCHEMA, ROLE_KEYS, SYSTEM_INSTRUCTIONS, TASK

__all__ = [
    "FlintFrameLabeller",
    "FLINT_ROLE_SCHEMA",
    "SYSTEM_INSTRUCTIONS",
    "ROLE_KEYS",
    "TASK",
    "role_dict_to_act_frame",
    "frames_from_sentences",
    "create_empty_flint_format",
    "create_empty_act_frame",
    "create_empty_fact_frame",
    "write_flint_frames_to_json",
]
