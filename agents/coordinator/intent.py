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
import re

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
GENERATE_PETRI_NET = "generate_petri_net"
VERIFY_EFLINT = "verify_eflint"
UNKNOWN = "unknown"

INTENTS = [
    NEW_PROJECT, ENGINEER_PROCESS, OPEN_PROJECT, LIST_PROJECTS, FIND_PROJECT,
    PROJECT_STATUS, REVISE_DESCRIPTION, DELETE_PROJECT, VERIFY_MODEL, GENERATE_PETRI_NET,
    VERIFY_EFLINT, UNKNOWN,
]

# A BPMN 2.0 document pasted into a chat message (optionally with an XML declaration).
_BPMN_XML = re.compile(
    r"(?:<\?xml[^>]*\?>\s*)?<(?P<p>[\w.-]+:)?definitions\b.*?</(?P=p)?definitions\s*>", re.DOTALL)
PETRI_NET_WORDS = ("petri", "pnml", "workflow net", "woped", "place/transition")
VERIFY_WORDS = ("verify", "verification", "check", "comply", "complies", "compliant", "compliance",
                "conform", "violat")
_NORM_WORDS = re.compile(r"\be-?flint\b|\bnorm(?:s|ative)?\b|\bregulat\w*|\blegal\b|\blaws?\b|"
                         r"\bqualification\b", re.IGNORECASE)
_BOUND = re.compile(r"\b(?:bound|max[_ -]?states|depth)\s*(?:of|=|:|is)?\s*(\d+)|\b(\d+)\s+steps\b",
                    re.IGNORECASE)

# Artifacts for eFLINT verification, pasted as fenced code blocks (```eflint ... ```). The fence
# language names the kind; unlabelled fences are recognised by their content.
ATTACHMENT_KINDS = ("eflint", "qualification", "analysis", "pnml", "pn")
_FENCE_LANGS = {
    "eflint": "eflint", "flint": "eflint",
    "qr": "qualification", "qualification": "qualification", "rules": "qualification",
    "analysis": "analysis", "ltl": "analysis", "properties": "analysis",
    "pnml": "pnml", "pn": "pn", "petri": "pn", "petri-net": "pn", "petrinet": "pn",
}
_FENCE = re.compile(r"```[ \t]*(?P<lang>[\w.+-]*)[^\n]*\n(?P<body>.*?)\n?```", re.DOTALL)
_PNML_XML = re.compile(r"(?:<\?xml[^>]*\?>\s*)?<(?P<p>[\w.-]+:)?pnml\b.*?</(?P=p)?pnml\s*>", re.DOTALL)


def extract_bpmn_xml(text: str) -> tuple[str, str | None]:
    """Split a message into ``(prose, bpmn_xml)``. Done in code, not by the classifier, so an
    attached model is forwarded verbatim and never echoed through an LLM."""
    m = _BPMN_XML.search(text)
    if not m:
        return text, None
    return (text[:m.start()] + text[m.end():]).strip(), m.group(0)


def _sniff(body: str) -> str | None:
    """Guess the kind of an unlabelled fenced block from its content."""
    b = body.strip()
    if re.search(r"<(?:[\w.-]+:)?pnml\b", b):
        return "pnml"
    if re.search(r"^\s*Qualify as\b", b, re.MULTILINE):
        return "qualification"
    if re.match(r"(?://[^\n]*\n\s*)*(?:Property|Compliance-checking|Plan)\b", b):
        return "analysis"
    if re.search(r"^\s*(?:Fact|Act|Event|Duty|Placeholder|Invariant|Predicate|#require)\b", b, re.MULTILINE):
        return "eflint"
    if re.search(r"^\s*place\s", b, re.MULTILINE) and re.search(r"^\s*transition\s", b, re.MULTILINE):
        return "pn"
    return None


def extract_attachments(text: str) -> tuple[str, dict[str, str]]:
    """Split eFLINT-verification artifacts off a message: ``(prose, {kind: source})``.

    Recognises fenced code blocks (labelled ``eflint`` / ``qualification`` (``qr``) / ``analysis``
    / ``pnml`` / ``pn``, or unlabelled and recognisable) and a bare PNML document. Like
    :func:`extract_bpmn_xml`, this is code, not the classifier: artifacts are forwarded verbatim.
    """
    found: dict[str, str] = {}

    def take(m: re.Match) -> str:
        lang = m.group("lang").lower()
        kind = _FENCE_LANGS.get(lang) or (_sniff(m.group("body")) if lang in ("", "xml", "text", "txt") else None)
        if kind is None or kind in found:
            return m.group(0)
        found[kind] = m.group("body").strip() + "\n"
        return ""

    prose = _FENCE.sub(take, text)
    if "pnml" not in found:
        m = _PNML_XML.search(prose)
        if m:
            found["pnml"] = m.group(0)
            prose = prose[:m.start()] + prose[m.end():]
    return (re.sub(r"\n{3,}", "\n\n", prose).strip() if found else text), found


def _description_in_command(text: str) -> str:
    """The process description carried by a command message, if any: the part after a colon
    ("Make a Petri net for: ..."), or the whole message when it is long enough to be one."""
    _, sep, tail = text.partition(":")
    if sep and len(tail.split()) >= 3:
        return tail.strip()
    return text.strip() if len(text.split()) >= 15 else ""


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

        # Verification of the process against an eFLINT/normative specification. Checked before
        # the Petri net rule: "verify the Petri net against the eFLINT norms" is a verification.
        attached = (context or {}).get("attachments") or []
        if hit(*VERIFY_WORDS) and (_NORM_WORDS.search(text) or "eflint" in attached):
            m = _BOUND.search(text)
            if m:
                slots["max_states"] = int(m.group(1) or m.group(2))
            return IntentResult(intent=VERIFY_EFLINT, slots=slots, confidence=0.8)

        # First: Petri net requests often embed a process description that would otherwise trip
        # the keyword rules below ("list", "status", "process", ...).
        if hit(*PETRI_NET_WORDS):
            description = _description_in_command(text)
            return IntentResult(intent=GENERATE_PETRI_NET,
                                slots={"process_description": description} if description else {},
                                confidence=0.8)
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
        "max_states": {"type": "integer", "minimum": 0},
    },
    "required": ["intent"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You are the intent router for an agentic workflow planner. Classify the user's message into "
    "exactly one intent and extract any relevant slots. Do not answer the request; only classify.\n"
    "- engineer_process: model a described process as BPMN.\n"
    "- generate_petri_net: translate a process description, an attached BPMN model, or the current "
    "project's model into a Petri net / workflow net / PNML. Put only the process description "
    "itself (if the message contains one) in process_description, without the instruction.\n"
    "- verify_eflint: verify/check the process (its Petri net or BPMN model) against an eFLINT / "
    "normative / regulatory specification, i.e. compliance or model checking with qualification "
    "rules. Put a requested search bound (number of steps) in max_states.\n"
    "- verify_model: verify/check the current model (no normative specification mentioned)."
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
