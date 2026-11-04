"""The coordinator: intent -> project resolution -> state-gated action -> route -> persist.

Flow per message (COORDINATOR_PLAN sec 6):

1. classify intent + slots;
2. resolve the project (create new / open existing / find similar via the semantic cache);
3. map the intent to a lifecycle *action* and check the state machine allows it — this is what
   *constrains the response* (a request that the current state forbids is refused, not run);
4. route to the sub-agent through the registry;
5. apply the state transition, record history, and persist.

A BPMN 2.0 document pasted into the message is split off *before* classification (in code) and
forwarded verbatim to the agent that needs it; the classifier only sees the prose. The same holds
for eFLINT-verification artifacts pasted as fenced code blocks (eFLINT spec, qualification rules,
analysis, PNML/.pn Petri net).
"""
from __future__ import annotations

import re
import uuid

from pydantic import BaseModel, Field

from agents.contract import AgentRequest, AgentResult, AgentRegistry
from agents.contract.models import JobStatus

from .cache.base import SemanticProjectCache, SimilarProject
from .cache.noop import NoOpSemanticCache
from . import handoff as H
from .handoff import ModelHandoff
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
        handoff: ModelHandoff | None = None,
    ):
        self.registry = registry
        # Where generated models are shared with downstream agents; None disables the hand-off.
        self.handoff = handoff
        self.store = store
        self.machine = machine
        self.cache = cache or NoOpSemanticCache()
        self.classifier = classifier or HeuristicIntentClassifier()

    # --- public entry point ----------------------------------------------------
    def handle_message(
        self, user_id: str, text: str, *, project_id: str | None = None
    ) -> CoordinatorResponse:
        prose, bpmn_xml = I.extract_bpmn_xml(text)
        prose, attachments = I.extract_attachments(prose)
        context = {"project_id": project_id}
        if bpmn_xml:
            context["attachment"] = "a BPMN 2.0 XML model (removed from the message text)"
        if attachments:
            context["attachments"] = sorted(attachments)
        result = self.classifier.classify(prose, context=context)
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
        if intent == I.VERIFY_EFLINT:
            return self._verify_eflint(user_id, pid, slots, attachments, bpmn_xml)
        if intent == I.VERIFY_MODEL:
            return self._verify(user_id, pid, slots, attachments, bpmn_xml)
        if intent == I.GENERATE_PETRI_NET:
            return self._petri_net(user_id, pid, slots, bpmn_xml)
        return CoordinatorResponse(
            reply="I couldn't tell what you'd like to do. You can start a new project, describe a "
            "process to model, open or list your projects, translate a model to a Petri net, or ask "
            "to verify a model (e.g. against an eFLINT specification).",
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
        if self.handoff:
            self.handoff.remove(project.id)
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
        if self.handoff and result.output.get("bpmn_xml"):
            self.handoff.save_bpmn(project.id, result.output["bpmn_xml"])
            self.handoff.discard(project.id, H.PNML)  # the Petri net of the old model is stale
        self._apply(project, I.ENGINEER_PROCESS, action, agent=result.agent, summary="Generated/updated BPMN model")
        self.store.save(project)
        self.cache.index_project(project)
        return CoordinatorResponse(
            reply=f"Generated a BPMN model for '{project.name}' (now {project.state}).",
            intent=I.ENGINEER_PROCESS, project=project.ref(), state=project.state,
            allowed_actions=self.machine.allowed_actions(project.state), agent_result=result,
        )

    def _verify(self, user_id: str, pid: str | None, slots: dict | None = None,
                attachments: dict | None = None, bpmn_xml: str | None = None) -> CoordinatorResponse:
        project = self._load(user_id, pid)
        if project is None:
            return self._no_project(I.VERIFY_MODEL)
        # The eFLINT verifier is the verification agent whenever the project has its inputs.
        if self.registry.get_for_intent(I.VERIFY_EFLINT) is not None and (
                (attachments or {}).get("eflint") or (self.handoff and self.handoff.load(project.id, H.EFLINT))):
            return self._verify_eflint(user_id, pid, slots or {}, attachments or {}, bpmn_xml)
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

    def _petri_net(self, user_id: str, pid: str | None, slots: dict, bpmn_xml: str | None) -> CoordinatorResponse:
        """Forward a BPMN/text -> Petri net job. Source priority: BPMN attached to the message, a
        description in the message, the active project's BPMN model, the project's description.

        Producing a Petri net is a derived view of the model, so it records history but does not
        move the project through the lifecycle (and is not gated by it).
        """
        project = self._load(user_id, pid)
        description = (slots.get("process_description") or "").strip()
        model = self.handoff.load_bpmn(project.id) if (project and self.handoff) else None
        if bpmn_xml:
            payload, source = {"input_type": "bpmn_xml", "bpmn_xml": bpmn_xml}, "the attached BPMN model"
        elif description:
            payload, source = {"input_type": "text", "text": description}, "your description"
        elif model:
            payload, source = {"input_type": "bpmn_xml", "bpmn_xml": model}, f"the BPMN model of '{project.name}'"
        elif project and project.description.strip():
            payload, source = {"input_type": "text", "text": project.description}, \
                f"the description of '{project.name}'"
        else:
            return self._reply(project, I.GENERATE_PETRI_NET, needs_input=True, reply=(
                "To build a Petri net, paste a BPMN 2.0 model, describe the process "
                "(e.g. 'Petri net for: ...'), or open a project that has a model."))

        agent = self.registry.get_for_intent(I.GENERATE_PETRI_NET)
        if agent is None:
            return self._reply(project, I.GENERATE_PETRI_NET, needs_input=True,
                               reply="The Petri net generator agent is not available.")
        if project:
            payload["model_name"] = project.name
        result = agent.invoke(AgentRequest(
            intent=I.GENERATE_PETRI_NET,
            project_id=project.id if project else None,
            correlation_id=uuid.uuid4().hex,
            payload={**payload, "output_formats": ["pnml"]},
        ))
        out = result.output or {}

        if not result.ok:
            reply = f"The Petri net could not be generated from {source}: {result.error}"
            summary = f"Petri net generation failed: {result.error}"
        elif out.get("status") == "needs_clarification":
            questions = " ".join(f"({i}) {a['question']}" for i, a in enumerate(out.get("ambiguities", []), 1)
                                 if a.get("severity") == "blocking")
            reply = (f"Before I can build the Petri net, please clarify: {questions} "
                     "Then send the description again with the answers.")
            summary = "Petri net generation needs clarification"
        else:
            n, v = out.get("net_summary") or {}, out.get("validation") or {}
            soundness = {"passed": "the soundness check passed",
                         "failed": f"the soundness check FAILED ({v.get('soundness_detail', '')})",
                         }.get(v.get("soundness"), "soundness was not checked")
            reply = (f"Generated a workflow Petri net from {source}: {n.get('places')} places, "
                     f"{n.get('transitions')} transitions ({n.get('silent_transitions')} silent); {soundness}.")
            if out.get("assumptions"):
                reply += " Assumptions: " + "; ".join(out["assumptions"])
            summary = f"Generated Petri net (PNML run {result.artifacts_ref})"

        if project:
            if result.ok and result.artifacts_ref:
                project.runs.append(result.artifacts_ref)
            if result.ok and self.handoff and out.get("pnml"):
                self.handoff.save(project.id, H.PNML, out["pnml"])
            project.record(HistoryEntry(intent=I.GENERATE_PETRI_NET, agent=result.agent, summary=summary))
            self.store.save(project)
        return self._reply(project, I.GENERATE_PETRI_NET, reply=reply, agent_result=result,
                           needs_input=not result.ok or out.get("status") == "needs_clarification")

    # --- eFLINT verification ---------------------------------------------------------------
    def _verify_eflint(self, user_id: str, pid: str | None, slots: dict, attachments: dict,
                       bpmn_xml: str | None) -> CoordinatorResponse:
        """Verify the process (Petri net) against an eFLINT specification via the eflint_verifier.

        Inputs come from the message (fenced blocks / pasted PNML or BPMN) or, failing that, the
        active project's hand-off area; anything attached is stored there for the next run. With
        no Petri net but a BPMN model, the Petri net generator is called first. Without an analysis
        document the verifier checks compliance (no normative violation within the bound).

        A conclusive verdict on a project that is MODEL_READY_FOR_VERIFICATION drives the lifecycle
        (startVerification -> completeVerificationSuccess/Failure); otherwise the check is recorded
        in the history as an ad-hoc run without changing the state.
        """
        intent = I.VERIFY_EFLINT
        project = self._load(user_id, pid)
        agent = self.registry.get_for_intent(I.VERIFY_EFLINT)
        if agent is None:
            return self._reply(project, intent, needs_input=True,
                               reply="The eFLINT verifier agent is not available.")
        store = self.handoff if (project and self.handoff) else None
        if store:  # remember what the user attached, for "verify again"
            for kind, filename in _ATTACHMENT_FILES.items():
                if attachments.get(kind):
                    store.save(project.id, filename, attachments[kind])
                    if kind in ("pnml", "pn"):  # one physical model per project
                        store.discard(project.id, H.PETRI_NET_TEXT if kind == "pnml" else H.PNML)

        def pick(kind: str) -> tuple[str | None, str]:
            if attachments.get(kind):
                return attachments[kind], "attached"
            if store:
                content = store.load(project.id, _ATTACHMENT_FILES[kind])
                if content:
                    return content, "the project's"
            return None, ""

        notes: list[str] = []
        # 1. The physical model: attached/stored Petri net, else derived from a BPMN model.
        physical, physical_from = None, ""
        pnml, origin = pick("pnml")
        pn, pn_origin = pick("pn")
        if attachments.get("pn") and not attachments.get("pnml"):
            pnml = None
        if pnml:
            physical, physical_from = {"language": "petri-net", "format": "pnml", "source": pnml}, f"{origin} Petri net"
        elif pn:
            physical, physical_from = {"language": "petri-net", "format": "pn", "source": pn}, f"{pn_origin} Petri net"
        else:
            bpmn = bpmn_xml or (store.load_bpmn(project.id) if store else None)
            if bpmn:
                generated = self._generate_pnml(project, bpmn)
                if isinstance(generated, str):
                    origin = "attached" if bpmn_xml else "the project's"
                    physical = {"language": "petri-net", "format": "pnml", "source": generated}
                    physical_from = f"the Petri net generated from {origin} BPMN model"
                    notes.append("I generated the Petri net from the BPMN model first.")
                else:
                    return self._reply(project, intent, needs_input=True, agent_result=generated, reply=(
                        f"I couldn't derive a Petri net from the BPMN model: {generated.error}"))

        eflint, eflint_origin = pick("eflint")
        rules, _ = pick("qualification")
        analysis, analysis_origin = pick("analysis")

        missing = [label for label, value in (("a process model (PNML / .pn Petri net, or a BPMN model)", physical),
                                              ("an eFLINT specification", eflint),
                                              ("qualification rules", rules)) if not value]
        if missing:
            return self._reply(project, intent, needs_input=True,
                               reply=self._missing_inputs_reply(agent, missing, physical, eflint))

        payload = {"physical": physical, "normative": {"language": "eflint", "source": eflint},
                   "qualification": rules}
        if analysis:
            payload["analysis"] = analysis
        if slots.get("max_states") is not None:
            payload["options"] = {"max_states": int(slots["max_states"])}
        result = agent.invoke(AgentRequest(intent=I.VERIFY_EFLINT, project_id=project.id if project else None,
                                           correlation_id=uuid.uuid4().hex, payload=payload))
        out = result.output or {}

        if not result.ok:
            reply = f"The eFLINT verification could not be completed: {result.error}"
            summary = f"eFLINT verification error: {result.error}"
        else:
            verdict = out.get("verdict")
            headline = {"passed": "Verification PASSED", "failed": "Verification FAILED",
                        }.get(verdict, "Verification was INCONCLUSIVE")
            reply = (f"{headline} — {physical_from} checked against {eflint_origin} eFLINT specification "
                     f"({out.get('operation')}): {out.get('summary')}.")
            if out.get("analysis_generated"):
                notes.append("No analysis was supplied, so I checked that no normative violation occurs in any "
                             f"execution over all {len(out.get('physical_transitions', []))} process transitions.")
            elif analysis_origin == "the project's":
                notes.append("I used the project's stored analysis document.")
            if verdict == "passed" and out.get("operation") != "plan":
                notes.append("This is a bounded check: it holds up to the search bound, not for longer executions.")
            summary = f"eFLINT verification {verdict}: {out.get('summary')}"
        if out.get("renamed"):
            pairs = list(out["renamed"].items())
            shown = ", ".join(f"'{a}' → {b}" for a, b in pairs[:8]) + (" …" if len(pairs) > 8 else "")
            notes.append(f"Petri net names were normalised for the verifier ({shown}); rules and analyses use the new names.")

        if project:
            if result.artifacts_ref:
                project.runs.append(result.artifacts_ref)
            verdict = out.get("verdict") if result.ok else None
            if (verdict in ("passed", "failed") and out.get("operation") in ("model-check", "compliance-check")
                    and self.machine.can(project.state, "startVerification")):
                self._apply(project, intent, "startVerification", agent=result.agent,
                            summary="Started eFLINT verification")
                done = "completeVerificationSuccess" if verdict == "passed" else "completeVerificationFailure"
                self._apply(project, intent, done, agent=result.agent, summary=summary)
                notes.append(f"The project is now {project.state}.")
            else:
                project.record(HistoryEntry(intent=intent, agent=result.agent, summary=summary))
            self.store.save(project)
        if notes:
            reply += " " + " ".join(notes)
        return self._reply(project, intent, reply=reply, agent_result=result,
                           needs_input=not result.ok or out.get("verdict") != "passed")

    def _generate_pnml(self, project: Project | None, bpmn_xml: str) -> str | AgentResult:
        """BPMN -> PNML through the Petri net generator; the PNML, or the failed AgentResult."""
        petri = self.registry.get_for_intent(I.GENERATE_PETRI_NET)
        if petri is None:
            return AgentResult(agent="coordinator", intent=I.GENERATE_PETRI_NET, status=JobStatus.failed,
                               error="the Petri net generator agent is not available")
        payload = {"input_type": "bpmn_xml", "bpmn_xml": bpmn_xml, "output_formats": ["pnml"]}
        if project:
            payload["model_name"] = project.name
        result = petri.invoke(AgentRequest(intent=I.GENERATE_PETRI_NET, project_id=project.id if project else None,
                                           correlation_id=uuid.uuid4().hex, payload=payload))
        pnml = (result.output or {}).get("pnml")
        if not result.ok or not pnml:
            if result.ok:
                result = result.model_copy(update={"status": JobStatus.failed, "error": result.error or (
                    "the generator returned no inline PNML" if (result.output or {}).get("status") != "needs_clarification"
                    else "the BPMN model is ambiguous; generate the Petri net first to answer its questions")})
            return result
        if project and self.handoff:
            self.handoff.save(project.id, H.PNML, pnml)
            if result.artifacts_ref:
                project.runs.append(result.artifacts_ref)
        return pnml

    def _missing_inputs_reply(self, agent, missing: list[str], physical: dict | None, eflint: str | None) -> str:
        reply = ("To verify the process against an eFLINT specification I still need "
                 + ", ".join(missing) + ". Paste each as a fenced code block in your message — ```eflint, "
                 "```qualification (rules mapping process steps to eFLINT acts/events, ending in '.'), "
                 "optionally ```analysis (properties; otherwise I check that no violation occurs), and "
                 "```pnml or ```pn for the Petri net. They are kept with the project for later runs.")
        if physical is not None:  # tell the user the (normalised) transition names to use in rules
            probe = agent.invoke(AgentRequest(intent=I.VERIFY_EFLINT, correlation_id=uuid.uuid4().hex,
                                              payload={"operation": "validate", "physical": physical}))
            names = (probe.output or {}).get("physical_transitions")
            if names:
                reply += " Process transitions: " + ", ".join(names) + "."
        if eflint:
            acts = re.findall(r"^\s*(?:Act|Event)\s+([A-Za-z_][\w-]*)", eflint, flags=re.MULTILINE)
            if acts:
                reply += " eFLINT acts/events: " + ", ".join(dict.fromkeys(acts)) + "."
        return reply

    # --- helpers ---------------------------------------------------------------
    def _reply(self, project: Project | None, intent: str, *, reply: str, needs_input: bool = False,
               agent_result: AgentResult | None = None) -> CoordinatorResponse:
        """A response that may or may not concern a project."""
        if project is None:
            return CoordinatorResponse(reply=reply, intent=intent, needs_input=needs_input, agent_result=agent_result)
        return CoordinatorResponse(
            reply=reply, intent=intent, project=project.ref(), state=project.state,
            allowed_actions=self.machine.allowed_actions(project.state),
            needs_input=needs_input, agent_result=agent_result,
        )


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


_ATTACHMENT_FILES = {"eflint": H.EFLINT, "qualification": H.QUALIFICATION, "analysis": H.ANALYSIS,
                     "pnml": H.PNML, "pn": H.PETRI_NET_TEXT}


def _derive_name(description: str) -> str:
    first_line = (description or "").strip().splitlines()[0] if description.strip() else ""
    first_line = first_line.lstrip("# ").strip()
    if not first_line:
        return "Untitled project"
    return (first_line[:60] + "…") if len(first_line) > 60 else first_line
