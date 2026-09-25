"""The coordinator: intent -> project resolution -> state-gated action -> route -> persist.

Flow per message (COORDINATOR_PLAN sec 6):

1. classify intent + slots;
2. resolve the project (create new / open existing / find similar via the semantic cache);
3. map the intent to a lifecycle *action* and check the state machine allows it — this is what
   *constrains the response* (a request that the current state forbids is refused, not run);
4. route to the sub-agent through the registry;
5. apply the state transition, record history, and persist.
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from agents.contract import AgentRequest, AgentResult, AgentRegistry

from .cache.base import SemanticProjectCache, SimilarProject
from .cache.noop import NoOpSemanticCache
from . import intent as I
from .intent import HeuristicIntentClassifier, IntentClassifier
from .projects.models import HistoryEntry, Project, ProjectRef
from .projects.store import ProjectNotFoundError, ProjectStore
from .state.machine import StateMachine, StateMachineError


class CoordinatorResponse(BaseModel):
    reply: str
    intent: str
    project: ProjectRef | None = None
    state: str | None = None
    allowed_actions: list[str] = Field(default_factory=list)
    agent_result: AgentResult | None = None
    similar: list[SimilarProject] = Field(default_factory=list)
    needs_input: bool = False


# How a recognized intent maps onto a lifecycle action, given the current state.
def _engineer_action(state: str) -> str | None:
    if state in ("INTENT_CAPTURED", "DESCRIPTION_DRAFTED"):
        return "createModel"
    if state in ("MODEL_IN_PROGRESS", "MODEL_READY_FOR_VERIFICATION", "VERIFICATION_FAILED"):
        return "refineModel"
    return None  # VERIFIED / IMPLEMENTATION_GENERATED / ... : engineering is gated off


class Coordinator:
    def __init__(
        self,
        *,
        registry: AgentRegistry,
        store: ProjectStore,
        machine: StateMachine,
        cache: SemanticProjectCache | None = None,
        classifier: IntentClassifier | None = None,
    ):
        self.registry = registry
        self.store = store
        self.machine = machine
        self.cache = cache or NoOpSemanticCache()
        self.classifier = classifier or HeuristicIntentClassifier()

    # --- public entry point ----------------------------------------------------
    def handle_message(
        self, user_id: str, text: str, *, project_id: str | None = None
    ) -> CoordinatorResponse:
        result = self.classifier.classify(text, context={"project_id": project_id})
        intent, slots = result.intent, result.slots
        pid = project_id or slots.get("project_id")

        if intent == I.LIST_PROJECTS:
            return self._list(user_id)
        if intent == I.FIND_PROJECT:
            return self._find(user_id, slots.get("query", text))
        if intent == I.NEW_PROJECT:
            return self._new_project(user_id, slots, text)
        if intent in (I.OPEN_PROJECT, I.PROJECT_STATUS):
            return self._status(user_id, pid, intent)
        if intent == I.DELETE_PROJECT:
            return self._delete(user_id, pid)
        if intent == I.REVISE_DESCRIPTION:
            return self._revise(user_id, pid, slots)
        if intent == I.ENGINEER_PROCESS:
            return self._engineer(user_id, pid, slots, text)
        if intent == I.VERIFY_MODEL:
            return self._verify(user_id, pid)
        return CoordinatorResponse(
            reply="I couldn't tell what you'd like to do. You can start a new project, describe a "
            "process to model, open or list your projects, or ask to verify a model.",
            intent=I.UNKNOWN,
            needs_input=True,
        )

    # --- intent handlers -------------------------------------------------------
    def _list(self, user_id: str) -> CoordinatorResponse:
        refs = self.store.list(user_id)
        names = ", ".join(f"{r.name} [{r.state}]" for r in refs) or "none yet"
        return CoordinatorResponse(reply=f"You have {len(refs)} project(s): {names}.", intent=I.LIST_PROJECTS)

    def _find(self, user_id: str, query: str) -> CoordinatorResponse:
        similar = self.cache.find_similar(query, user_id=user_id)
        if not similar:  # fall back to naive substring search until the cache is implemented
            refs = self.store.search(user_id, query)
            similar = [SimilarProject(ref=r, score=0.0) for r in refs]
        if similar:
            listed = ", ".join(s.ref.name for s in similar)
            return CoordinatorResponse(reply=f"Found similar project(s): {listed}.", intent=I.FIND_PROJECT, similar=similar)
        return CoordinatorResponse(reply="No similar projects found.", intent=I.FIND_PROJECT)

    def _new_project(self, user_id: str, slots: dict, text: str) -> CoordinatorResponse:
        description = slots.get("process_description", text)
        # Offer existing/similar projects before creating a duplicate (no-op today -> []).
        similar = self.cache.find_similar(description, user_id=user_id)
        if similar and not slots.get("force"):
            listed = ", ".join(s.ref.name for s in similar)
            return CoordinatorResponse(
                reply=f"Similar project(s) already exist: {listed}. Open one, or say 'new project' "
                "again to create a fresh one.",
                intent=I.NEW_PROJECT, similar=similar, needs_input=True,
            )
        project = self._create_project(user_id, slots.get("project_name"), description)
        # If a description was supplied, advance INTENT_CAPTURED -> DESCRIPTION_DRAFTED.
        if description.strip():
            self._apply(project, I.NEW_PROJECT, "createDescription", summary="Captured description")
        self.store.save(project)
        self.cache.index_project(project)
        return self._ok(project, I.NEW_PROJECT, f"Created project '{project.name}'.")

    def _status(self, user_id: str, pid: str | None, intent: str) -> CoordinatorResponse:
        project = self._load(user_id, pid)
        if project is None:
            return self._no_project(intent)
        return self._ok(project, intent, f"Project '{project.name}' is in state {project.state}.")

    def _delete(self, user_id: str, pid: str | None) -> CoordinatorResponse:
        project = self._load(user_id, pid)
        if project is None:
            return self._no_project(I.DELETE_PROJECT)
        self.machine.apply(project.state, "deleteProject")  # validates it's allowed
        self.store.delete(user_id, project.id)
        self.cache.remove(project.id)
        return CoordinatorResponse(reply=f"Deleted project '{project.name}'.", intent=I.DELETE_PROJECT)

    def _revise(self, user_id: str, pid: str | None, slots: dict) -> CoordinatorResponse:
        project = self._load(user_id, pid)
        if project is None:
            return self._no_project(I.REVISE_DESCRIPTION)
        project.description = slots.get("process_description", project.description)
        changed = bool(slots.get("semantics_changed"))
        try:
            tr = self._apply(
                project, I.REVISE_DESCRIPTION, "reviseDescription",
                semantics_changed=changed, summary="Revised description",
            )
        except StateMachineError as exc:
            return self._refused(project, I.REVISE_DESCRIPTION, str(exc))
        self.store.save(project)
        self.cache.index_project(project)
        note = " Prior verification/approvals were invalidated; re-verification is required." if tr.invalidated else ""
        return self._ok(project, I.REVISE_DESCRIPTION, f"Updated description.{note}")

    def _engineer(self, user_id: str, pid: str | None, slots: dict, text: str) -> CoordinatorResponse:
        description = slots.get("process_description", text)
        project = self._load(user_id, pid)
        if project is None:  # implicit new project from the description
            project = self._create_project(user_id, slots.get("project_name"), description)
            self._apply(project, I.ENGINEER_PROCESS, "createDescription", summary="Captured description")

        action = _engineer_action(project.state)
        if action is None or not self.machine.can(project.state, action):
            return self._refused(
                project, I.ENGINEER_PROCESS,
                f"Engineering a model isn't applicable in state {project.state}.",
            )

        agent = self.registry.get_for_intent("engineer_process")
        if agent is None:
            return self._refused(project, I.ENGINEER_PROCESS, "The text->BPMN agent is not available.")

        result = agent.invoke(
            AgentRequest(
                intent="engineer_process",
                project_id=project.id,
                correlation_id=uuid.uuid4().hex,
                payload={"text": description, "process_name": project.name},
            )
        )
        if not result.ok:
            project.record(HistoryEntry(intent=I.ENGINEER_PROCESS, agent=result.agent, summary=f"Agent failed: {result.error}"))
            self.store.save(project)
            return CoordinatorResponse(
                reply=f"The model could not be generated: {result.error}",
                intent=I.ENGINEER_PROCESS, project=project.ref(), state=project.state,
                allowed_actions=self.machine.allowed_actions(project.state), agent_result=result,
            )

        if result.artifacts_ref:
            project.runs.append(result.artifacts_ref)
        self._apply(project, I.ENGINEER_PROCESS, action, agent=result.agent, summary="Generated/updated BPMN model")
        self.store.save(project)
        self.cache.index_project(project)
        return CoordinatorResponse(
            reply=f"Generated a BPMN model for '{project.name}' (now {project.state}).",
            intent=I.ENGINEER_PROCESS, project=project.ref(), state=project.state,
            allowed_actions=self.machine.allowed_actions(project.state), agent_result=result,
        )

    def _verify(self, user_id: str, pid: str | None) -> CoordinatorResponse:
        project = self._load(user_id, pid)
        if project is None:
            return self._no_project(I.VERIFY_MODEL)
        if not self.machine.can(project.state, "startVerification"):
            return self._refused(
                project, I.VERIFY_MODEL,
                f"Verification isn't applicable in state {project.state}; mark the model ready first.",
            )
        agent = self.registry.get_for_intent("verify_model")
        if agent is None:
            return self._refused(
                project, I.VERIFY_MODEL,
                "A verification agent isn't wired in yet (planned external agent).",
            )
        # (When a verify agent exists, invoke it here and transition on its verdict.)
        return self._refused(project, I.VERIFY_MODEL, "Verification agent not implemented.")

    # --- helpers ---------------------------------------------------------------
    def _create_project(self, user_id: str, name: str | None, description: str) -> Project:
        pid = uuid.uuid4().hex[:12]
        project = Project(
            id=pid,
            user_id=user_id,
            name=name or _derive_name(description),
            description=description,
            state=self.machine.initial_state,
        )
        self.store.create(project)
        return project

    def _apply(self, project: Project, intent: str, action: str, *, semantics_changed: bool = False, agent: str | None = None, summary: str = ""):
        tr = self.machine.apply(project.state, action, semantics_changed=semantics_changed)
        project.record(HistoryEntry(
            intent=intent, action=action, agent=agent,
            from_state=tr.from_state, to_state=tr.to_state, summary=summary,
        ))
        project.state = tr.to_state
        return tr

    def _load(self, user_id: str, pid: str | None) -> Project | None:
        if not pid:
            return None
        try:
            return self.store.get(user_id, pid)
        except ProjectNotFoundError:
            return None

    def _ok(self, project: Project, intent: str, reply: str) -> CoordinatorResponse:
        return CoordinatorResponse(
            reply=reply, intent=intent, project=project.ref(), state=project.state,
            allowed_actions=self.machine.allowed_actions(project.state),
        )

    def _refused(self, project: Project, intent: str, reply: str) -> CoordinatorResponse:
        return CoordinatorResponse(
            reply=reply, intent=intent, project=project.ref(), state=project.state,
            allowed_actions=self.machine.allowed_actions(project.state), needs_input=True,
        )

    def _no_project(self, intent: str) -> CoordinatorResponse:
        return CoordinatorResponse(
            reply="I couldn't find that project. Provide a project id or start a new project.",
            intent=intent, needs_input=True,
        )


def _derive_name(description: str) -> str:
    first_line = (description or "").strip().splitlines()[0] if description.strip() else ""
    first_line = first_line.lstrip("# ").strip()
    if not first_line:
        return "Untitled project"
    return (first_line[:60] + "…") if len(first_line) > 60 else first_line
