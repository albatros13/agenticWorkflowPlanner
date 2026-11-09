"""Intent recognition for the coordinator.

Two implementations behind one :class:`IntentClassifier` protocol:

* :class:`HeuristicIntentClassifier` — keyword rules, no network. Default so the coordinator
  (and its tests) run fully offline.
* :class:`LLMIntentClassifier` — uses an :class:`~agents.text_to_bpmn.pipeline.llm.base.LLMProvider` with *structured*
  JSON output (never free prose that gets parsed), consistent with the rest of the pipeline.

The LLM decides *classification*; the coordinator decides *control flow*.
"""
from __future__ import annotations

import abc
import logging
import re
from functools import lru_cache

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

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
GENERATE_EFLINT = "generate_eflint"
VALIDATE_EFLINT = "validate_eflint"
UPLOAD_DOCUMENT = "upload_document"
FIND_DOCUMENTS = "find_documents"
VECTORIZE_DOCUMENT = "vectorize_document"
LIST_VECTORIZED = "list_vectorized"
SEARCH_VECTORS = "search_vectors"
LIST_CAPABILITIES = "list_capabilities"
UNKNOWN = "unknown"

# What the coordinator answers itself, whichever agents are registered.
BUILTIN_INTENTS = [
    NEW_PROJECT, OPEN_PROJECT, LIST_PROJECTS, FIND_PROJECT, PROJECT_STATUS, REVISE_DESCRIPTION,
    DELETE_PROJECT, VERIFY_MODEL, LIST_CAPABILITIES, UNKNOWN,
]
INTENTS = [
    *BUILTIN_INTENTS, ENGINEER_PROCESS, GENERATE_PETRI_NET, VERIFY_EFLINT, GENERATE_EFLINT,
    VALIDATE_EFLINT, UPLOAD_DOCUMENT, FIND_DOCUMENTS, VECTORIZE_DOCUMENT, LIST_VECTORIZED,
    SEARCH_VECTORS,
]

# A BPMN 2.0 document pasted into a chat message (optionally with an XML declaration).
_BPMN_XML = re.compile(
    r"(?:<\?xml[^>]*\?>\s*)?<(?P<p>[\w.-]+:)?definitions\b.*?</(?P=p)?definitions\s*>", re.DOTALL)
_NORM_WORDS = re.compile(r"\be-?flint\b|\bnorm(?:s|ative)?\b|\bregulat\w*|\blegal\b|\blaws?\b|"
                         r"\bqualification\b", re.IGNORECASE)
_BOUND = re.compile(r"\b(?:bound|max[_ -]?states|depth)\s*(?:of|=|:|is)?\s*(\d+)|\b(\d+)\s+steps\b",
                    re.IGNORECASE)

# Artifacts for eFLINT verification, pasted as fenced code blocks (```eflint ... ```). The fence
# language names the kind; unlabelled fences are recognised by their content.
_FENCE_LANGS = {
    "document": "document", "doc": "document", "regulation": "document", "law": "document",
    "markdown": "document", "md": "document",
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


def text_after_colon(text: str) -> str:
    """The content a command message introduces with a colon ("Upload this regulation: ...")."""
    _, sep, tail = text.partition(":")
    return tail.strip() if sep and len(tail.split()) >= 3 else ""


def description_in_command(text: str) -> str:
    """The description carried by a command message, if any: the part after a colon ("Make a
    Petri net for: ..."), or the whole message when it is long enough to be one."""
    return text_after_colon(text) or (text.strip() if len(text.split()) >= 15 else "")


def clean_document_ref(ref: str) -> str:
    """Reduce "the GDPR document" to the part that can match a library title or id."""
    ref = ref.strip().strip("\"'“”.,;:?! ")
    ref = re.sub(r"\s+(?:in|from|into|to)\s+(?:the\s+)?(?:library|qdrant|vector\s+(?:store|database|db))$", "", ref, flags=re.I)
    while True:
        cleaned = re.sub(r"^(?:the|this|that|a|an|my|our|stored|uploaded|regulatory|legal)\s+", "", ref, flags=re.I)
        cleaned = re.sub(r"\s+(?:document|doc|regulation|regulations|file|text)$", "", cleaned, flags=re.I)
        if cleaned == ref:
            break
        ref = cleaned
    return "" if re.fullmatch(r"(?:documents?|docs?|regulations?|files?|text|it|one|this|that)?", ref, re.I) else ref


# --- Recognising commands for the regulatory document and eFLINT agents ---------------------------
# Anchored at the start of the message: process descriptions often mention documents and
# regulations ("the clerk uploads the document ..."), and those must stay process descriptions.
_MAX_COMMAND_WORDS = 20
_LEAD = (r"^\W*(?:(?:please|pls|kindly|hi|hey|ok|okay|now|then|can\s+you|could\s+you|would\s+you|will\s+you|"
         r"i\s+want\s+to|i'd\s+like\s+to|i\s+would\s+like\s+to|i\s+need\s+to|let's|lets|help\s+me(?:\s+to)?)[\s,]+)*")
_DOC = r"(?:documents?|docs?|regulations?|regulatory|legislation|laws?|policy|policies|directives?|gdpr)"
_SKIP = r"(?:(?!projects?\b)[\w'-]+\s+){0,4}?"  # "my project documents" is about projects
_RE = re.IGNORECASE

_CAPABILITIES = re.compile(r"^\W*(?:help|capabilities|agents)\W*$|\bwhat\s+(?:can|do)\s+you\s+(?:do|offer|support)\b|"
                           r"\b(?:your|agent|available)\s+capabilities\b|\b(?:which|what)\s+agents\b", _RE)
_GENERATE_EFLINT = re.compile(
    _LEAD + r"(?:generate|create|write|draft|formali[sz]e|translate|convert|derive|build|make(?!\s+sure)|produce|turn)\s+"
            r"(?:(?!verif|check|compl|against)[\w'-]+\s+){0,6}?e-?flint\b", _RE)
_VALIDATE_EFLINT = re.compile(_LEAD + r"(?:validate|lint|type-?check)\b.*\be-?flint\b", _RE | re.DOTALL)
_UPLOAD = re.compile(
    _LEAD + rf"(?:(?:upload|ingest)\s+{_SKIP}(?:{_DOC}\b|this\b|these\b|text\b|file\b|\S+\.(?:pdf|md|txt)\b)|"
            rf"(?:add|register|store|import|save)\s+{_SKIP}{_DOC}\b)", _RE)
_VECTORIZE = re.compile(_LEAD + rf"(?:vectori[sz]e\b|(?:embed|index)\s+{_SKIP}{_DOC}\b)", _RE)
_LIST_VECTORIZED = re.compile(
    _LEAD + r"(?:list|show|which|what|get|display)\b.*\b(?:vectori[sz]ed|embedded|indexed|qdrant|"
            r"vector\s+(?:store|database|db|collections?))\b", _RE)
_SEARCH = re.compile(
    _LEAD + rf"(?:semantic(?:ally)?\s+search\b|(?:search|query|look\s*up)\s+(?:in\s+|through\s+)?{_SKIP}{_DOC}\b|"
            rf"look\s+(?:through|in)\s+{_SKIP}{_DOC}\b|"
            r"find\s+(?:\w+\s+)?(?:passages?|clauses?|articles?|provisions?)\b|"
            rf"what\s+(?:do|does|did)\s+{_SKIP}{_DOC}\s+(?:say|state|require|mandate|prescribe)\b)", _RE)
_FIND = re.compile(
    _LEAD + rf"(?:(?:list|show|find|browse|get|display)\s+{_SKIP}{_DOC}\b|"
            r"(?:which|what)\s+(?:\w+\s+)?(?:documents|regulations|laws|policies)\b)", _RE)
_QUERY_TAIL = re.compile(r"\b(?:about|regarding|concerning|on|for|matching|containing|mentioning|called|named|"
                         r"titled)\s+(.+)$", _RE | re.DOTALL)
_SOURCE_TAIL = re.compile(r"\b(?:from|of|for)\s+(.+)$", _RE | re.DOTALL)
_DOC_ID = re.compile(r"\b[0-9a-f]{12}\b")
_PROFILE = re.compile(r"\b(?:(?:with|using|in)\s+)?(?:the\s+)?(?:profile\s+[\"']?(?P<a>[\w-]+)[\"']?|"
                      r"[\"']?(?P<b>[\w-]+)[\"']?\s+profile)\b", _RE)
_TOP = re.compile(r"\b(?:top|first|best)\s+(\d+)\b", _RE)
_TITLED = re.compile(r"\b(?:titled|called|named|title)\s*[:=]?\s*[\"“']([^\"”']+)[\"”']", _RE)


def _profile_slots(text: str) -> tuple[str, dict]:
    """Split a vectorization profile choice off a command: ``(rest, {"profile": ...})``."""
    m = _PROFILE.search(text)
    if not m:
        return text, {}
    return text[:m.start()] + text[m.end():], {"profile": m.group("a") or m.group("b")}


def _query_slot(prose: str, head: str) -> str:
    """What a search is for: the part after a colon, or after "about"/"for"/... in the command."""
    tail = prose.partition(":")[2].strip()
    if not tail:
        m = _QUERY_TAIL.search(head)
        tail = m.group(1) if m else ""
    return tail.strip().strip("\"'“”?!.,; ")


def _document_ref(head: str, pattern: re.Pattern | None = None) -> str:
    """A stored document named in a command: its id, a quoted title, or the rest of the command."""
    m = _DOC_ID.search(head)
    if m:
        return m.group(0)
    m = re.search(r"[\"“']([^\"”']+)[\"”']", head)
    if m:
        return m.group(1).strip()
    if pattern is None:
        return ""
    m = pattern.search(head)
    return clean_document_ref(m.group(1)) if m else ""


def _classify_agent_command(prose: str, attached: list[str]) -> IntentResult | None:
    """The regulatory document and eFLINT commands, or None when the message is something else."""
    head = prose.partition(":")[0].strip()
    if len(head.split()) > _MAX_COMMAND_WORDS:
        return None

    # eFLINT authoring first: "generate eFLINT from the regulation" is not a document operation.
    if _GENERATE_EFLINT.search(head):
        ref = _document_ref(head, _SOURCE_TAIL)
        return IntentResult(intent=GENERATE_EFLINT, slots={"document_ref": ref} if ref else {}, confidence=0.8)
    if _VALIDATE_EFLINT.search(head) or ("eflint" in attached and re.search(_LEAD + r"(?:validate|lint)\b", head, _RE)):
        if not re.search(r"\bagainst\b", head, _RE):
            return IntentResult(intent=VALIDATE_EFLINT, confidence=0.8)
        return IntentResult(intent=VERIFY_EFLINT, confidence=0.8)

    if "document" in attached or _UPLOAD.search(head):
        m = _TITLED.search(head)
        return IntentResult(intent=UPLOAD_DOCUMENT, slots={"title": m.group(1).strip()} if m else {}, confidence=0.8)
    if _LIST_VECTORIZED.search(head):
        return IntentResult(intent=LIST_VECTORIZED, slots=_profile_slots(head)[1], confidence=0.8)
    if _VECTORIZE.search(head):
        rest, slots = _profile_slots(head)
        after_verb = re.sub(_LEAD + r"(?:vectori[sz]e|embed|index)\b", "", rest, count=1, flags=_RE)
        ref = _document_ref(rest) or clean_document_ref(after_verb)
        return IntentResult(intent=VECTORIZE_DOCUMENT, slots={**slots, **({"document_ref": ref} if ref else {})},
                            confidence=0.8)
    if _SEARCH.search(head):
        rest, slots = _profile_slots(head)
        top = _TOP.search(rest)
        if top:
            slots["limit"] = int(top.group(1))
            rest = _TOP.sub("", rest)
        query = _query_slot(prose, rest)
        if not query:  # "semantic search data retention": no preposition before the topic
            m = _SEARCH.search(rest)
            query = rest[m.end():].strip(" ,;?!.") if m else ""
        if query:
            slots["query"] = query
        return IntentResult(intent=SEARCH_VECTORS, slots=slots, confidence=0.8)
    if _FIND.search(head):
        query = _query_slot(prose, head)
        return IntentResult(intent=FIND_DOCUMENTS, slots={"query": query} if query else {}, confidence=0.8)
    return None


class IntentResult(BaseModel):
    intent: str = UNKNOWN
    slots: dict = Field(default_factory=dict)
    confidence: float = 0.0


class IntentClassifier(abc.ABC):
    @abc.abstractmethod
    def classify(self, text: str, *, context: dict | None = None) -> IntentResult: ...


class HeuristicIntentClassifier(IntentClassifier):
    """Deterministic keyword classifier. Good enough offline; overridden by the LLM one in prod.

    ``registered_intents`` lets it recognise an agent intent it has no rule for by its name:
    "summarize document ..." for a registered ``summarize_document``.
    """

    def __init__(self, registered_intents: list[str] | None = None):
        self._named = {
            intent: re.compile(_LEAD + r"\s+".join(map(re.escape, intent.split("_"))) + r"\b", _RE)
            for intent in registered_intents or [] if intent not in INTENTS
        }

    def classify(self, text: str, *, context: dict | None = None) -> IntentResult:
        slots: dict = {}
        head = text.partition(":")[0]
        # Project/verification commands are short. A long message is a process description even
        # when it happens to contain "list", "status", "delete", "verify", ...
        command = len(head.split()) <= _MAX_COMMAND_WORDS

        if _CAPABILITIES.search(head):
            return IntentResult(intent=LIST_CAPABILITIES, confidence=0.8)
        for intent, pattern in self._named.items():
            if pattern.search(text):
                return IntentResult(intent=intent, confidence=0.7)

        attached = (context or {}).get("attachments") or []
        agent_command = _classify_agent_command(text, attached)
        if agent_command is not None:
            return agent_command

        # Verification of the process against an eFLINT/normative specification. Checked before
        # the Petri net rule: "verify the Petri net against the eFLINT norms" is a verification.
        if _VERIFY.search(text) and (_NORM_WORDS.search(text) or "eflint" in attached):
            m = _BOUND.search(text)
            if m:
                slots["max_states"] = int(m.group(1) or m.group(2))
            return IntentResult(intent=VERIFY_EFLINT, slots=slots, confidence=0.8)

        # First: Petri net requests often embed a process description that would otherwise trip
        # the keyword rules below ("list", "status", "process", ...).
        if _PETRI.search(text):
            description = description_in_command(text)
            return IntentResult(intent=GENERATE_PETRI_NET,
                                slots={"process_description": description} if description else {},
                                confidence=0.8)
        if command:
            if _LIST_PROJECTS.search(head):
                return IntentResult(intent=LIST_PROJECTS, confidence=0.8)
            # Destructive: the object must be named ("delete this project"), never inferred.
            if _DELETE_PROJECT.search(head):
                return IntentResult(intent=DELETE_PROJECT, confidence=0.8)
            if _phrase("find similar", "similar project", "existing project", "search project",
                       "any project about").search(head):
                return IntentResult(intent=FIND_PROJECT, slots={"query": text}, confidence=0.7)
            if _STATUS.search(head):
                return IntentResult(intent=PROJECT_STATUS, confidence=0.7)
            if _phrase("open project", "continue project", "resume project", "switch to project").search(head):
                return IntentResult(intent=OPEN_PROJECT, confidence=0.7)
            if _phrase("revise description", "revise the description", "update the description",
                       "change the description", "edit description").search(head):
                return IntentResult(
                    intent=REVISE_DESCRIPTION,
                    slots={"process_description": text,
                           "semantics_changed": bool(_phrase("actor", "new goal", "risk", "data use").search(text))},
                    confidence=0.7,
                )
            if _phrase("verify", "verification", "compliance check", "review the model").search(head):
                return IntentResult(intent=VERIFY_MODEL, confidence=0.7)
            if _phrase("new project", "start a project", "create a project", "start over").search(head):
                return IntentResult(intent=NEW_PROJECT, slots={"process_description": text}, confidence=0.7)
        if _phrase("bpmn", "process", "workflow", "diagram", "model this", "engineer", "generate").search(text):
            return IntentResult(intent=ENGINEER_PROCESS, slots={"process_description": text}, confidence=0.6)
        # A substantial free-text description with no command verbs -> treat as a process to engineer.
        if len(text.split()) >= 8:
            return IntentResult(intent=ENGINEER_PROCESS, slots={"process_description": text}, confidence=0.4)
        return IntentResult(intent=UNKNOWN, confidence=0.2)


def _phrase(*phrases: str) -> re.Pattern:
    """Whole-word, case-insensitive match of any of ``phrases`` ("list" must not match "specialist")."""
    return _phrase_cached(phrases)


@lru_cache(maxsize=None)
def _phrase_cached(phrases: tuple[str, ...]) -> re.Pattern:
    alternatives = "|".join(r"\s+".join(map(re.escape, p.split())) for p in phrases)
    return re.compile(rf"\b(?:{alternatives})\b", re.IGNORECASE)


_VERIFY = re.compile(r"\b(?:verif(?:y|ies|ied|ying|ication)|checks?|checked|checking|compl(?:y|ies|iant|iance)|"
                     r"conform\w*|violat\w*)\b", re.IGNORECASE)
_PETRI = re.compile(r"\bpetri\w*|\bpnml\b|\bworkflow[\s-]+nets?\b|\bwoped\b|\bplace/transition\b", re.IGNORECASE)
_LIST_PROJECTS = re.compile(_LEAD + r"(?:list|show|display)\b(?:\s+[\w']+){0,3}?\s+projects?\b|"
                            r"\b(?:my|all)\s+projects\b", re.IGNORECASE)
_DELETE_PROJECT = re.compile(r"\b(?:delete|remove|discard|drop)\s+(?:(?:this|the|my|that|current|our)\s+)?"
                             r"project\b", re.IGNORECASE)
_STATUS = re.compile(_LEAD + r"(?:(?:what(?:'s|\s+is)\s+the\s+|show\s+(?:the\s+|me\s+the\s+)?)?(?:status|progress)\b|"
                     r"what\s+state\b|where\s+are\s+we\b)|\bproject(?:'s)?\s+(?:status|state|progress)\b",
                     re.IGNORECASE)


_SYSTEM_HEADER = (
    "You are the intent router for an agentic workflow planner. Classify the user's message into "
    "exactly one intent and extract any relevant slots. Do not answer the request; only classify. "
    "A long description of how a process runs is engineer_process even if it mentions documents, "
    "regulations or searching. The intents:"
)

# Routing guidance for the intents the coordinator knows. An intent of a newly registered agent has
# no entry here: it is described to the router by its agent's manifest, so it needs no code change.
_GUIDANCE = {
    NEW_PROJECT: "start a new project.",
    OPEN_PROJECT: "open or resume a project.",
    LIST_PROJECTS: "list the user's projects (not documents).",
    FIND_PROJECT: "find an existing or similar project; put the search text in query.",
    PROJECT_STATUS: "report the state of the current project.",
    REVISE_DESCRIPTION: "change the project's description; set semantics_changed when actors, goals, "
                        "risks or data use change.",
    DELETE_PROJECT: "delete the current project.",
    LIST_CAPABILITIES: "the user asks what you can do or which agents are available.",
    ENGINEER_PROCESS: "model a described process as BPMN.",
    GENERATE_PETRI_NET: "translate a process description, an attached BPMN model, or the current "
                        "project's model into a Petri net / workflow net / PNML. Put only the process "
                        "description itself (if the message contains one) in process_description, "
                        "without the instruction.",
    VERIFY_EFLINT: "verify/check the process (its Petri net or BPMN model) against an eFLINT / "
                   "normative / regulatory specification, i.e. compliance or model checking with "
                   "qualification rules. Put a requested search bound (number of steps) in max_states.",
    VERIFY_MODEL: "verify/check the current model (no normative specification mentioned).",
    GENERATE_EFLINT: "formalise regulatory text as a new eFLINT specification. This writes a "
                     "specification; checking a process against one is verify_eflint. Put the id or "
                     "title of a stored document, if one is named, in document_ref.",
    VALIDATE_EFLINT: "check that an eFLINT specification itself is well-formed and run its scenarios "
                     "(no process model involved).",
    UPLOAD_DOCUMENT: "add a regulatory document to the library. Put a requested title in title.",
    FIND_DOCUMENTS: "list or look up documents in the regulatory library by title or description "
                    "(not their content); put the search words, if any, in query.",
    VECTORIZE_DOCUMENT: "vectorize/embed/index a stored document into the vector database. Put its id "
                        "or title in document_ref and a named vectorization profile in profile.",
    LIST_VECTORIZED: "list which documents are vectorized / stored in Qdrant. Put a named profile in "
                     "profile.",
    SEARCH_VECTORS: "search the content of regulatory documents, or ask what a regulation says on a "
                    "topic. Put the topic in query, a requested number of results in limit and a "
                    "named profile in profile.",
    UNKNOWN: "none of the above.",
}


def _schema(intents: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": intents},
            "project_name": {"type": "string"},
            "process_description": {"type": "string"},
            "query": {"type": "string"},
            "semantics_changed": {"type": "boolean"},
            "max_states": {"type": "integer", "minimum": 0},
            "document_ref": {"type": "string"},
            "title": {"type": "string"},
            "profile": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1},
        },
        "required": ["intent"],
        "additionalProperties": False,
    }


def _system_prompt(intents: list[str], descriptions: dict[str, str]) -> str:
    lines = [f"- {intent}: {_GUIDANCE.get(intent) or descriptions.get(intent) or intent.replace('_', ' ')}"
             for intent in intents]
    return _SYSTEM_HEADER + "\n" + "\n".join(lines)


class LLMIntentClassifier(IntentClassifier):
    """``capabilities`` (from :meth:`AgentRegistry.capabilities`) limit the agent intents to the
    available agents and describe those without built-in guidance. Without them, every intent the
    coordinator knows is offered.

    The model's answer is untrusted input: an intent outside the offered set becomes ``unknown``
    and slots of the wrong type are dropped. When the provider fails, the message is classified by
    the heuristic ``fallback`` instead of failing the request.
    """

    def __init__(self, provider, capabilities: list | None = None,
                 fallback: IntentClassifier | None = None):
        self._provider = provider
        if capabilities is None:
            intents, descriptions = list(INTENTS), {}
        else:
            descriptions = {c.intent: c.description for c in capabilities}
            intents = [*BUILTIN_INTENTS, *(i for i in descriptions if i not in BUILTIN_INTENTS)]
        self._intents = set(intents)
        self._schema = _schema(intents)
        self._system = _system_prompt(intents, descriptions)
        self._fallback = fallback or HeuristicIntentClassifier(
            registered_intents=[i for i in intents if i not in INTENTS])

    def classify(self, text: str, *, context: dict | None = None) -> IntentResult:
        ctx = f"\nContext: {context}" if context else ""
        try:
            result = self._provider.generate_json(
                task="intent_classification",
                system=self._system,
                prompt=f"User message:\n{text}{ctx}",
                schema=self._schema,
            )
        except Exception as exc:  # noqa: BLE001 - a classifier outage must not fail the request
            log.warning("LLM intent classification failed, using the heuristic classifier: %s", exc)
            return self._fallback.classify(text, context=context)
        data = result.data if isinstance(result.data, dict) else {}
        intent = data.get("intent")
        if intent not in self._intents:
            intent = UNKNOWN
        return IntentResult(intent=intent, slots=_typed_slots(data, self._schema), confidence=1.0)


def _typed_slots(data: dict, schema: dict) -> dict:
    """The slots of ``data`` that match their declared JSON type (ints may arrive as strings)."""
    slots: dict = {}
    for key, raw in data.items():
        spec = schema["properties"].get(key)
        if key == "intent" or spec is None or raw is None:
            continue
        value = _coerce_slot(raw, spec)
        if value is not None:
            slots[key] = value
    return slots


def _coerce_slot(raw, spec: dict):
    """``raw`` as the slot's JSON type, or None when it does not fit."""
    kind = spec.get("type")
    if kind == "integer":
        if isinstance(raw, bool):
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        return value if value >= spec.get("minimum", value) else None
    if kind == "boolean":
        return raw if isinstance(raw, bool) else None
    if kind == "string":
        return raw if isinstance(raw, str) else None
    return raw
