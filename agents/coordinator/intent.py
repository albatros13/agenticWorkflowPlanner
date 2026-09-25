"""Intent recognition for the coordinator.

Two implementations behind one :class:`IntentClassifier` protocol:

* :class:`HeuristicIntentClassifier` — keyword rules, no network. Default so the coordinator
  (and its tests) run fully offline.
* :class:`LLMIntentClassifier` — uses an :class:`~src.llm.base.LLMProvider` with *structured*
  JSON output (never free prose that gets parsed), consistent with the rest of the pipeline.

The LLM decides *classification*; the coordinator decides *control flow*.
"""
from __future__ import annotations

import abc

from pydantic import BaseModel, Field

# Canonical coordinator intents.
NEW_PROJECT = "new_project"
ENGINEER_PROCESS = "engineer_process"
OPEN_PROJECT = "open_project"
LIST_PROJECTS = "list_projects"
FIND_PROJECT = "find_project"
PROJECT_STATUS = "project_status"
REVISE_DESCRIPTION = "revise_description"
DELETE_PROJECT = "delete_project"
VERIFY_MODEL = "verify_model"
UNKNOWN = "unknown"

INTENTS = [
    NEW_PROJECT, ENGINEER_PROCESS, OPEN_PROJECT, LIST_PROJECTS, FIND_PROJECT,
    PROJECT_STATUS, REVISE_DESCRIPTION, DELETE_PROJECT, VERIFY_MODEL, UNKNOWN,
]


class IntentResult(BaseModel):
    intent: str = UNKNOWN
    slots: dict = Field(default_factory=dict)
    confidence: float = 0.0


class IntentClassifier(abc.ABC):
    @abc.abstractmethod
    def classify(self, text: str, *, context: dict | None = None) -> IntentResult: ...


class HeuristicIntentClassifier(IntentClassifier):
    """Deterministic keyword classifier. Good enough offline; overridden by the LLM one in prod."""

    def classify(self, text: str, *, context: dict | None = None) -> IntentResult:
        t = text.lower().strip()
        slots: dict = {}

        def hit(*words: str) -> bool:
            return any(w in t for w in words)

        if hit("list", "show my projects", "my projects", "all projects"):
            return IntentResult(intent=LIST_PROJECTS, confidence=0.8)
        if hit("delete", "remove project", "discard project"):
            return IntentResult(intent=DELETE_PROJECT, confidence=0.8)
        if hit("find similar", "similar project", "existing project", "search project", "any project about"):
            return IntentResult(intent=FIND_PROJECT, slots={"query": text}, confidence=0.7)
        if hit("status", "what state", "where are we", "progress"):
            return IntentResult(intent=PROJECT_STATUS, confidence=0.7)
        if hit("open project", "continue project", "resume project", "switch to project"):
            return IntentResult(intent=OPEN_PROJECT, confidence=0.7)
        if hit("revise description", "update the description", "change the description", "edit description"):
            return IntentResult(
                intent=REVISE_DESCRIPTION,
                slots={"process_description": text, "semantics_changed": hit("actor", "new goal", "risk", "data use")},
                confidence=0.7,
            )
        if hit("verify", "verification", "compliance check", "review the model"):
            return IntentResult(intent=VERIFY_MODEL, confidence=0.7)
        if hit("new project", "start a project", "create a project", "start over"):
            return IntentResult(intent=NEW_PROJECT, slots={"process_description": text}, confidence=0.7)
        if hit("bpmn", "process", "workflow", "diagram", "model this", "engineer", "generate"):
            return IntentResult(intent=ENGINEER_PROCESS, slots={"process_description": text}, confidence=0.6)
        # A substantial free-text description with no command verbs -> treat as a process to engineer.
        if len(text.split()) >= 8:
            return IntentResult(intent=ENGINEER_PROCESS, slots={"process_description": text}, confidence=0.4)
        return IntentResult(intent=UNKNOWN, confidence=0.2)


_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": INTENTS},
        "project_name": {"type": "string"},
        "process_description": {"type": "string"},
        "query": {"type": "string"},
        "semantics_changed": {"type": "boolean"},
    },
    "required": ["intent"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You are the intent router for an agentic workflow planner. Classify the user's message into "
    "exactly one intent and extract any relevant slots. Do not answer the request; only classify."
)


class LLMIntentClassifier(IntentClassifier):
    def __init__(self, provider):
        self._provider = provider

    def classify(self, text: str, *, context: dict | None = None) -> IntentResult:
        ctx = f"\nContext: {context}" if context else ""
        result = self._provider.generate_json(
            task="intent_classification",
            system=_SYSTEM,
            prompt=f"User message:\n{text}{ctx}",
            schema=_SCHEMA,
        )
        data = result.data
        intent = data.get("intent", UNKNOWN)
        slots = {k: v for k, v in data.items() if k != "intent"}
        return IntentResult(intent=intent, slots=slots, confidence=1.0)
