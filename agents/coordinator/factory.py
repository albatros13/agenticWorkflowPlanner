"""Assemble a :class:`Coordinator` from config + environment.

Chooses in-process vs HTTP sub-agents from ``config/agents.json``, the project store backend,
the semantic cache implementation (``SEMANTIC_CACHE``), and the intent classifier (LLM when a
provider is configured, heuristic otherwise). Everything is overridable for tests.
"""
from __future__ import annotations

import os

from agents.contract import AgentRegistry
from agents.paths import AGENTS_CONFIG, PROJECT_STATES_CONFIG
from agents.text_to_bpmn.agent import TextToBpmnHandler

from .cache import build_cache
from .coordinator import Coordinator
from .intent import HeuristicIntentClassifier, IntentClassifier, LLMIntentClassifier
from .projects.json_store import JsonProjectStore
from .projects.store import ProjectStore
from .state.machine import StateMachine


def _default_classifier() -> IntentClassifier:
    """Use the LLM classifier when a key is present; otherwise stay fully offline."""
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY"):
        try:
            from agents.text_to_bpmn.pipeline.config import Settings
            from agents.text_to_bpmn.shaping import select_provider

            return LLMIntentClassifier(select_provider(Settings.from_env()))
        except Exception:
            pass
    return HeuristicIntentClassifier()


def build_coordinator(
    *,
    store: ProjectStore | None = None,
    classifier: IntentClassifier | None = None,
) -> Coordinator:
    handlers = {"text_to_bpmn": TextToBpmnHandler()}
    registry = AgentRegistry.from_config(AGENTS_CONFIG, handlers=handlers)
    machine = StateMachine.from_config(PROJECT_STATES_CONFIG)
    cache = build_cache(os.getenv("SEMANTIC_CACHE", "noop"))
    return Coordinator(
        registry=registry,
        store=store or JsonProjectStore(),
        machine=machine,
        cache=cache,
        classifier=classifier or _default_classifier(),
    )
