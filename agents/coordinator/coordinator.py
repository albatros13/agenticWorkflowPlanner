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
        if intent == I.LIST_CAPABILITIES:
            return CoordinatorResponse(reply=self._capabilities_text(), intent=intent)

        project = self._load(user_id, pid)
        if intent == I.GENERATE_EFLINT:
            return self._generate_eflint(project, slots, prose, attachments)
        if intent == I.VALIDATE_EFLINT:
            return self._validate_eflint(project, attachments)
        if intent == I.UPLOAD_DOCUMENT:
            return self._upload_document(project, slots, prose, attachments)
        if intent == I.FIND_DOCUMENTS:
            return self._find_documents(project, slots)
        if intent == I.VECTORIZE_DOCUMENT:
            return self._vectorize_document(project, slots)
        if intent == I.LIST_VECTORIZED:
            return self._list_vectorized(project, slots)
        if intent == I.SEARCH_VECTORS:
            return self._search_vectors(project, slots)
        if intent in self.registry.intents():  # an agent registered without a dedicated route
            return self._route_registered(project, intent, slots, prose, attachments, bpmn_xml)
        return CoordinatorResponse(
            reply="I couldn't tell what you'd like to do. " + self._capabilities_text(),
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
        if not eflint and self.registry.get_for_intent(I.GENERATE_EFLINT) is not None:
            reply += " I can also generate the eFLINT specification from a regulation: ask me to 'generate eFLINT from <document>'."
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

    # --- eFLINT authoring ------------------------------------------------------------------
    def _generate_eflint(self, project: Project | None, slots: dict, prose: str, attachments: dict) -> CoordinatorResponse:
        """Formalise regulatory text as eFLINT. Source priority: a pasted document, text after a
        colon, a named library document, the message itself when it is long enough to be the text.

        A specification that passes its checks becomes the project's spec for ``verify_eflint``;
        one that needs review is returned but never replaces it. A derived artifact: recorded in
        the history, no lifecycle change.
        """
        intent = I.GENERATE_EFLINT
        ref = I.clean_document_ref(slots.get("document_ref") or "")
        text = attachments.get("document") or I.text_after_colon(prose)
        payload: dict = {}
        if text:
            payload["text"], source = text, "your text"
        elif ref:
            doc = self._resolve_document(project, intent, ref)
            if isinstance(doc, CoordinatorResponse):
                return doc
            payload["document_id"], source = doc["doc_id"], f"'{doc['title']}'"
            payload.setdefault("title", doc["title"])
        else:
            text = I.description_in_command(prose)
            if not text:
                return self._reply(project, intent, needs_input=True, reply=(
                    "To generate eFLINT I need the regulation: paste it in a ```regulation block, write it after "
                    "a colon ('Generate eFLINT: ...'), or name a document from the library ('generate eFLINT "
                    "from <title or id>')."))
            payload["text"], source = text, "your text"
        if slots.get("title"):
            payload["title"] = slots["title"]
        if attachments.get("eflint"):
            payload["base_spec"] = attachments["eflint"]
        if project and project.description.strip():
            payload["domain_context"] = project.description

        result = self._invoke(intent, payload, project)
        if result is None:
            return self._reply(project, intent, needs_input=True, reply="The eFLINT generator agent is not available.")
        out = result.output or {}
        if not result.ok:
            reply = f"The eFLINT specification could not be generated from {source}: {result.error}"
            summary = f"eFLINT generation failed: {result.error}"
        else:
            reply = f"Generated an eFLINT specification from {source}: {_check_summary(out)}."
            summary = f"Generated eFLINT specification ({out.get('status')}, run {result.artifacts_ref})"
            spec = (out.get("files") or {}).get("spec.eflint")
            if out.get("status") != "succeeded":
                reply += " It needs review, so I did not store it as the project's specification." + _top_errors(out)
            elif project and self.handoff and spec:
                had_rules = self.handoff.load(project.id, H.QUALIFICATION)
                self.handoff.save(project.id, H.EFLINT, spec)
                reply += " It is now the project's specification, ready for eFLINT verification."
                if had_rules:
                    reply += " Check that the project's qualification rules still match its acts and events."
        if project:
            if result.ok and result.artifacts_ref:
                project.runs.append(result.artifacts_ref)
            project.record(HistoryEntry(intent=intent, agent=result.agent, summary=summary))
            self.store.save(project)
        return self._reply(project, intent, reply=reply, agent_result=result,
                           needs_input=not result.ok or out.get("status") != "succeeded")

    def _validate_eflint(self, project: Project | None, attachments: dict) -> CoordinatorResponse:
        """Check a pasted specification, or the project's, without changing what is stored."""
        intent = I.VALIDATE_EFLINT
        source, origin = attachments.get("eflint"), "The attached"
        if not source and project and self.handoff:
            source, origin = self.handoff.load(project.id, H.EFLINT), "The project's"
        if not source:
            return self._reply(project, intent, needs_input=True, reply=(
                "To validate eFLINT, paste the specification in an ```eflint block or open a project that has one."))
        result = self._invoke(intent, {"eflint": source}, project)
        if result is None:
            return self._reply(project, intent, needs_input=True, reply="The eFLINT generator agent is not available.")
        out = result.output or {}
        if not result.ok:
            return self._reply(project, intent, needs_input=True, agent_result=result,
                               reply=f"The eFLINT specification could not be checked: {result.error}")
        verdict = "is valid" if out.get("status") == "succeeded" else "has problems"
        return self._reply(project, intent, agent_result=result, needs_input=out.get("status") != "succeeded",
                           reply=f"{origin} eFLINT specification {verdict}: {_check_summary(out)}.{_top_errors(out)}")

    # --- regulatory documents --------------------------------------------------------------
    def _upload_document(self, project: Project | None, slots: dict, prose: str, attachments: dict) -> CoordinatorResponse:
        """Add a pasted document to the library. Only pasted text: a chat message must not be able
        to make the server read a file from its own disk (the agent's ``path`` input stays with
        trusted callers such as its demo app)."""
        intent = I.UPLOAD_DOCUMENT
        text = attachments.get("document") or I.text_after_colon(prose)
        if not text:
            return self._reply(project, intent, needs_input=True, reply=(
                "To add a document, paste its text in a ```regulation block or after a colon ('Upload this "
                "regulation: ...'). Files (PDF, Markdown) can be uploaded in the regulatory_docs demo app."))
        result = self._invoke(intent, {"text": text, "title": slots.get("title") or _derive_name(text)}, project)
        if result is None:
            return self._docs_unavailable(project, intent)
        if not result.ok:
            return self._docs_failed(project, intent, result, "The document could not be added")
        doc = result.output["document"]
        self._record_docs(project, intent, result, f"Added document '{doc['title']}' ({doc['doc_id']})")
        return self._reply(project, intent, agent_result=result, reply=(
            f"Added '{doc['title']}' to the document library (id {doc['doc_id']}, {doc['n_blocks']} blocks). "
            f"Say 'vectorize document {doc['doc_id']}' to make it searchable."))

    def _find_documents(self, project: Project | None, slots: dict) -> CoordinatorResponse:
        intent = I.FIND_DOCUMENTS
        query = (slots.get("query") or "").strip()
        result = self._invoke(intent, {"query": query} if query else {}, project)
        if result is None:
            return self._docs_unavailable(project, intent)
        if not result.ok:
            return self._docs_failed(project, intent, result, "The document library could not be read")
        docs = result.output.get("documents", [])
        if not docs:
            reply = f"No documents match '{query}'." if query else "The document library is empty."
        else:
            matching = f" matching '{query}'" if query else ""
            reply = f"{len(docs)} document(s){matching}:\n" + _document_lines(docs)
        profiles = ", ".join(p["name"] + (" (default)" if p.get("is_default") else "")
                             for p in result.output.get("profiles", []))
        if docs and profiles:
            reply += f"\nVectorization profiles: {profiles}."
        return self._reply(project, intent, reply=reply, agent_result=result)

    def _vectorize_document(self, project: Project | None, slots: dict) -> CoordinatorResponse:
        intent = I.VECTORIZE_DOCUMENT
        doc = self._resolve_document(project, intent, I.clean_document_ref(slots.get("document_ref") or ""))
        if isinstance(doc, CoordinatorResponse):
            return doc
        payload = {"doc_id": doc["doc_id"]}
        if slots.get("profile"):
            payload["profile"] = slots["profile"]
        result = self._invoke(intent, payload, project)
        if result is None:
            return self._docs_unavailable(project, intent)
        if not result.ok:
            return self._docs_failed(project, intent, result, f"'{doc['title']}' could not be vectorized")
        v = result.output["vectorization"]
        self._record_docs(project, intent, result, f"Vectorized '{doc['title']}' into {v['collection']}")
        return self._reply(project, intent, agent_result=result, reply=(
            f"Vectorized '{doc['title']}': {v['points_upserted']} chunks in collection '{v['collection']}' "
            f"(cluster {v['cluster']}, model {v['embedding_model']}). You can now search it."))

    def _list_vectorized(self, project: Project | None, slots: dict) -> CoordinatorResponse:
        intent = I.LIST_VECTORIZED
        result = self._invoke(intent, {"profile": slots["profile"]} if slots.get("profile") else {}, project)
        if result is None:
            return self._docs_unavailable(project, intent)
        if not result.ok:
            return self._docs_failed(project, intent, result, "The vectorized documents could not be listed")
        lines = []
        for c in result.output.get("collections", []):
            where = f"{c['collection']} (cluster {c['cluster']})"
            if c.get("error"):
                lines.append(f"{where}: could not be read ({c['error']})")
            else:
                names = ", ".join(f"{d['title']} [{d['chunks']} chunks]" for d in c["documents"]) or "no documents"
                lines.append(f"{where}: {names}")
        return self._reply(project, intent, agent_result=result,
                           reply="\n".join(lines) or "No vectorization profiles are configured.")

    def _search_vectors(self, project: Project | None, slots: dict) -> CoordinatorResponse:
        intent = I.SEARCH_VECTORS
        query = (slots.get("query") or "").strip()
        if not query:
            return self._reply(project, intent, needs_input=True, reply=(
                "What should I search the regulatory documents for? For example: 'search the regulations "
                "for data retention'."))
        payload: dict = {"query": query}
        for slot in ("profile", "limit"):
            if slots.get(slot):
                payload[slot] = slots[slot]
        result = self._invoke(intent, payload, project)
        if result is None:
            return self._docs_unavailable(project, intent)
        if not result.ok:
            return self._docs_failed(project, intent, result, "The search could not be completed")
        hits = result.output.get("hits", [])
        if not hits:
            return self._reply(project, intent, agent_result=result, reply=(
                f"No passages found for '{query}' in collection '{result.output.get('collection')}'. "
                "Has a document been vectorized with this profile?"))
        return self._reply(project, intent, agent_result=result,
                           reply=f"Top {len(hits)} passage(s) for '{query}':\n" + _hit_lines(hits))

    def _resolve_document(self, project: Project | None, intent: str, ref: str) -> dict | CoordinatorResponse:
        """The one library record a reference (id, title, or nothing) means; else a question."""
        result = self._invoke(I.FIND_DOCUMENTS, {"query": ref} if ref else {}, project)
        if result is None:
            return self._docs_unavailable(project, intent)
        if not result.ok:
            return self._docs_failed(project, intent, result, "The document library could not be read")
        docs = result.output.get("documents", [])
        docs = [d for d in docs if ref.lower() in (d["doc_id"], d["title"].lower())] or docs
        if len(docs) == 1:
            return docs[0]
        if not docs:
            reply = f"No document in the library matches '{ref}'." if ref else "The document library is empty."
            reply += " Paste the text in a ```regulation block to add one."
        else:
            reply = "Which document do you mean?\n" + _document_lines(docs)
        return self._reply(project, intent, reply=reply, needs_input=True, agent_result=result)

    def _record_docs(self, project: Project | None, intent: str, result: AgentResult, summary: str) -> None:
        """The library is shared across projects; a change made from a project is noted in its history."""
        if project:
            project.record(HistoryEntry(intent=intent, agent=result.agent, summary=summary))
            self.store.save(project)

    def _docs_unavailable(self, project: Project | None, intent: str) -> CoordinatorResponse:
        return self._reply(project, intent, needs_input=True, reply="The regulatory documents agent is not available.")

    def _docs_failed(self, project: Project | None, intent: str, result: AgentResult, what: str) -> CoordinatorResponse:
        return self._reply(project, intent, needs_input=True, agent_result=result, reply=f"{what}: {result.error}")

    # --- capability discovery --------------------------------------------------------------
    def _capabilities_text(self) -> str:
        lines = "\n".join(f"- {c.intent.replace('_', ' ')} ({c.agent}): {c.description}"
                          for c in self.registry.capabilities())
        return ("I can manage your projects (new, open, list, find, status, revise, delete)"
                + (" and call these agents:\n" + lines if lines else "."))

    def _route_registered(self, project: Project | None, intent: str, slots: dict, prose: str,
                          attachments: dict, bpmn_xml: str | None) -> CoordinatorResponse:
        """Route an intent that has an agent but no dedicated handler, so that registering an agent
        in ``config/agents.json`` is enough to reach it. The agent gets the message and the slots,
        and validates them itself. No lifecycle change: that needs a dedicated handler."""
        payload = {**slots, "text": prose}
        if attachments:
            payload["attachments"] = attachments
        if bpmn_xml:
            payload["bpmn_xml"] = bpmn_xml
        result = self._invoke(intent, payload, project)
        label = intent.replace("_", " ")
        if not result.ok:
            reply, summary = f"'{label}' failed: {result.error}", f"{label} failed: {result.error}"
        else:
            detail = (result.output or {}).get("summary")
            reply = summary = f"{result.agent} completed '{label}'" + (f": {detail}" if detail else ".")
        if project:
            if result.ok and result.artifacts_ref:
                project.runs.append(result.artifacts_ref)
            project.record(HistoryEntry(intent=intent, agent=result.agent, summary=summary))
            self.store.save(project)
        return self._reply(project, intent, reply=reply, agent_result=result, needs_input=not result.ok)

    # --- helpers ---------------------------------------------------------------
    def _invoke(self, intent: str, payload: dict, project: Project | None) -> AgentResult | None:
        """Call the agent serving ``intent``; None when no such agent is available."""
        agent = self.registry.get_for_intent(intent)
        if agent is None:
            return None
        return agent.invoke(AgentRequest(intent=intent, project_id=project.id if project else None,
                                         correlation_id=uuid.uuid4().hex, payload=payload))

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


def _check_summary(output: dict) -> str:
    """The counts of a text_to_eflint check report, in words."""
    s = output.get("summary") or {}
    text = f"{s.get('errors', 0)} error(s), {s.get('warnings', 0)} warning(s)"
    if s.get("scenarios_total"):
        text += f", {s.get('scenarios_passed', 0)}/{s['scenarios_total']} scenarios passed"
    return text


def _top_errors(output: dict, limit: int = 5) -> str:
    errors = [f.get("message", "") for f in output.get("findings", []) if f.get("severity") == "error"]
    return (" Errors: " + "; ".join(errors[:limit]) + ("; …" if len(errors) > limit else "") + ".") if errors else ""


def _document_lines(docs: list[dict]) -> str:
    return "\n".join(f"- {d['title']} (id {d['doc_id']}" + (", vectorized)" if d.get("vectorized") else ")")
                     for d in docs)


def _hit_lines(hits: list[dict], snippet_chars: int = 240) -> str:
    lines = []
    for n, hit in enumerate(hits, 1):
        where = " — ".join(p for p in (hit.get("title"), hit.get("section")) if p) or "?"
        text = " ".join((hit.get("text") or "").split())
        snippet = text[:snippet_chars] + ("…" if len(text) > snippet_chars else "")
        lines.append(f"{n}. {where} (score {hit.get('score', 0):.3f}): {snippet}")
    return "\n".join(lines)


def _derive_name(description: str) -> str:
    first_line = (description or "").strip().splitlines()[0] if description.strip() else ""
    first_line = first_line.lstrip("# ").strip()
    if not first_line:
        return "Untitled project"
    return (first_line[:60] + "…") if len(first_line) > 60 else first_line
