"""Assemble a :class:`Coordinator` from config + environment.

Chooses in-process vs HTTP sub-agents from ``config/agents.json``, the project store backend,
the semantic cache implementation (``SEMANTIC_CACHE``), and the intent classifier (LLM when a
provider is configured, heuristic otherwise). Everything is overridable for tests.
"""
from __future__ import annotations

import logging
import os

from agents.contract import AgentRegistry
from agents.eflint_verifier.agent import EflintVerifierHandler
from agents.paths import AGENTS_CONFIG, PROJECT_STATES_CONFIG
from agents.petri_net_generator.agent import PetriNetGeneratorHandler
from agents.regulatory_docs.agent import RegulatoryDocsHandler
from agents.text_to_bpmn.agent import TextToBpmnHandler
from agents.text_to_bpmn.pipeline.config import load_dotenv
from agents.text_to_bpmn.shaping import default_provider
from agents.text_to_eflint.agent import TextToEflintHandler

from .cache import build_cache
from .coordinator import Coordinator
from .handoff import ModelHandoff
from .intent import HeuristicIntentClassifier, IntentClassifier, LLMIntentClassifier
from .projects.json_store import JsonProjectStore
from .projects.store import ProjectStore
from .state.machine import StateMachine

log = logging.getLogger(__name__)


def _default_classifier(registry: AgentRegistry) -> IntentClassifier:
    """Use the LLM classifier when a key is present; otherwise stay fully offline. Either way the
    classifier learns what to recognise from the registry.

    ``.env`` is loaded *before* looking for a key: the keys usually live only there.
    """
    heuristic = HeuristicIntentClassifier(registered_intents=registry.intents())
    load_dotenv()
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY"):
        try:
            return LLMIntentClassifier(default_provider(), capabilities=registry.capabilities(),
                                       fallback=heuristic)
        except Exception as exc:  # noqa: BLE001 - degrade, but say so
            log.warning("LLM intent classifier unavailable (%s: %s); using the heuristic classifier.",
                        type(exc).__name__, exc)
    else:
        log.info("No LLM API key configured; using the heuristic intent classifier.")
    return heuristic


def build_coordinator(
    *,
    store: ProjectStore | None = None,
    classifier: IntentClassifier | None = None,
) -> Coordinator:
    handlers = {
        "text_to_bpmn": TextToBpmnHandler(),
        "petri_net_generator": PetriNetGeneratorHandler(),  # PM4Py loads lazily on first use
        "text_to_eflint": TextToEflintHandler(),  # LLM provider resolved per request
        "regulatory_docs": RegulatoryDocsHandler(),  # library, embedder and Qdrant load lazily
        # Client of the external eFLINT model-checking service (EFLINT_VERIFIER_URL, default :8100).
        "eflint_verifier": EflintVerifierHandler(),
    }
    registry = AgentRegistry.from_config(AGENTS_CONFIG, handlers=handlers)
    machine = StateMachine.from_config(PROJECT_STATES_CONFIG)
    cache = build_cache(os.getenv("SEMANTIC_CACHE", "noop"))
    return Coordinator(
        registry=registry,
        store=store or JsonProjectStore(),
        machine=machine,
        cache=cache,
        classifier=classifier or _default_classifier(registry),
        handoff=ModelHandoff(),
    )
