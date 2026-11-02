"""Prompts + output schema for LLM-driven eFLINT generation and repair.

The modelling guidance encodes the approach of the reference data-governance framework
(``reference/``): an ontology of atomic agent/object types, one fact per legal condition with its
article, duties created by the act that triggers the obligation and terminated by the act that
fulfils it, acts whose preconditions combine the legal conditions, and scenarios that exercise
the compliant path plus one violation per key norm.
"""
from __future__ import annotations

import json

from .checks import Report
from .spec import EflintSpec

TASK_DRAFT = "eflint_spec"
TASK_REPAIR = "eflint_repair"

_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}
_GROUNDED = {
    "source": {"type": "string", "description": "Citation, e.g. 'GDPR Art. 5(1)(b)'; 'domain model' for process state not in the text."},
    "source_quote": {"type": "string", "description": "Verbatim span copied from the input text (<= 300 chars). Empty for 'domain model'."},
    "explanation": {"type": "string", "description": "Short label for the comment above the declaration."},
}
_STATEMENT = {"type": "object", "properties": {"term": _STR, "comment": _STR}, "required": ["term"]}

SPEC_SCHEMA: dict = {
    "type": "object",
    "description": "An executable eFLINT specification of the regulatory text.",
    "properties": {
        "title": _STR,
        "facts": {"type": "array", "items": {"type": "object", "properties": {
            "name": _STR,
            "identified_by": {**_STRS, "description": "['String'] or ['Int'] for atomic types, else the component type names in argument order."},
            "holds_when": {"type": ["string", "null"], "description": "Optional derivation condition (eFLINT expression)."},
            **_GROUNDED,
        }, "required": ["name", "identified_by", "source"]}},
        "duties": {"type": "array", "items": {"type": "object", "properties": {
            "name": _STR, "holder": _STR, "claimant": _STR, "related_to": _STRS,
            "violated_when": {"type": ["string", "null"], "description": "eFLINT expression over the duty's parameters."},
            **_GROUNDED,
        }, "required": ["name", "holder", "claimant", "violated_when", "source"]}},
        "acts": {"type": "array", "items": {"type": "object", "properties": {
            "name": _STR, "actor": _STR, "recipient": {"type": ["string", "null"]}, "related_to": _STRS,
            "holds_when": {"type": ["string", "null"], "description": "Precondition (eFLINT expression over the act's parameters)."},
            "creates": {**_STRS, "description": "Terms created, e.g. 'consent()' or 'f(controller, purpose)'."},
            "terminates": _STRS,
            **_GROUNDED,
        }, "required": ["name", "actor", "source"]}},
        "instances": {"type": "array", "items": _STATEMENT, "description": "Ground atomic instances, e.g. controller(\"company-a\")."},
        "initial_state": {"type": "array", "items": _STATEMENT, "description": "Ground composite facts true at the start."},
        "scenarios": {"type": "array", "items": {"type": "object", "properties": {
            "name": _STR, "description": _STR,
            "steps": {"type": "array", "items": {"type": "object", "properties": {
                "kind": {"type": "string", "enum": ["assert", "retract", "perform", "query"]},
                "term": {"type": "string", "description": "Ground term with literal arguments."},
                "query": {"type": "string", "enum": ["holds", "enabled", "violated"]},
                "expect": {"type": "boolean", "description": "query: expected truth; perform: expected to be enabled."},
                "comment": _STR,
            }, "required": ["kind", "term"]}},
        }, "required": ["name", "steps"]}},
    },
    "required": ["title", "facts", "duties", "acts", "instances", "initial_state", "scenarios"],
}

SYSTEM = """\
You are a legal knowledge engineer. You formalise regulatory text as an executable eFLINT \
specification (facts, duties, acts, initial state, scenarios) and return it as JSON matching \
the schema. Your output is parsed, type-checked and executed; every scenario must pass.

MODELLING APPROACH
1. Ontology: one atomic type per kind of agent, object or value (controller, data-subject, data, \
purpose, ...): identified_by ["String"] (or ["Int"] for counters such as rounds). Names are \
lowercase kebab-case, singular nouns.
2. Legal conditions: one fact per condition the text imposes (lawfulness, purpose limitation, \
consent, ...), identified_by the atomic types it relates (e.g. ["controller", "purpose"]).
3. Process state: facts recording what has happened (e.g. personal-data-collection); their source \
is "domain model".
4. Duties ("shall", "must", "is obliged to"): holder, claimant, related_to and violated_when \
(when the obligation is breached). Every duty is created by the act that triggers it and \
terminated by the act that fulfils it.
5. Acts (powers/permissions: "may", "has the right to", or the action a duty requires): actor, \
recipient, related_to; holds_when is the conjunction of the preconditions in the text; creates \
and terminates list the effects. Withdrawal/revocation is a separate act that terminates.
6. Instances and initial state: realistic ground terms for the scenario domain.
7. Scenarios: (a) one compliant path performing the acts in order, with queries confirming each \
effect; (b) one scenario per key norm where a condition is missing, showing a disabled act \
(perform with expect=false or query enabled with expect=false) or a violated duty (query \
violated with expect=true). Expectations must follow from the legal text.

EFLINT SUBSET (anything else is rejected)
- Variables are the parameter names of the enclosing declaration, which are type names \
(controller, purpose); use a suffix for a second parameter of the same type (controller2). A fact's \
parameters are its identified_by types; an atomic fact's single parameter is its own name.
- Argument order: act = actor, recipient, related_to...; duty = holder, claimant, related_to...; \
fact = identified_by order.
- f() with empty parentheses fills every argument from the same-named variable in scope.
- Operators: &&, ||, !, parentheses, ==, !=, <, <=, >, >=, Holds(t), Enabled(t), Violated(t), \
(Exists x : e), (Forall x : e), True, False, "string", 123.
- Fields hold expressions or terms only: no 'Holds when' keyword, no trailing '.'.
- Never put holds_when on a String/Int type; postulate its instances instead.
- Instance/scenario terms are ground: every argument is a quoted string or an integer.

ACCURACY AND GROUNDING
- Model only what the text says; do not invent obligations, exceptions or conditions.
- Every non-atomic fact, duty and act cites its article/paragraph in source and copies a \
verbatim source_quote from the input. Process-state facts use source "domain model".
- When the text is ambiguous, choose the most literal reading and say so in explanation.
"""


def _dump(spec: EflintSpec) -> str:
    return json.dumps(spec.model_dump(mode="json", exclude_defaults=True), ensure_ascii=False)


def draft_prompt(
    text: str,
    *,
    reference: EflintSpec | None = None,
    base: EflintSpec | None = None,
    domain_context: str = "",
    title: str = "",
    frame_hints: list[dict] | None = None,
    scenarios: bool = True,
) -> str:
    parts: list[str] = []
    if reference is not None:
        parts += [
            "REFERENCE EXAMPLE — an expert-written specification of GDPR / Data Act / Electricity "
            "Directive norms for a federated-learning training round. Follow its modelling style "
            "(not its domain):",
            _dump(reference), "",
        ]
    if base is not None:
        parts += [
            "EXISTING SPECIFICATION — extend it: keep its declarations and names unless the text "
            "contradicts them, and return the complete extended specification:",
            _dump(base), "",
        ]
    if frame_hints:
        parts += [
            "CANDIDATE ACT FRAMES — semantic roles labelled per normative sentence (hints; verify "
            "against the text):",
            json.dumps(frame_hints, ensure_ascii=False), "",
        ]
    if domain_context.strip():
        parts += [f"APPLICATION CONTEXT (use for instances and scenarios): {domain_context.strip()}", ""]
    if title.strip():
        parts += [f"TITLE: {title.strip()}", ""]
    if not scenarios:
        parts += ["Return an empty scenarios list.", ""]
    parts += ["REGULATORY TEXT:", "<<<", text.strip(), ">>>", "",
              "Produce the eFLINT specification for this text."]
    return "\n".join(parts)


def repair_prompt(text: str, spec: EflintSpec, report: Report, *, max_findings: int = 40) -> str:
    issues = [f for f in report.findings if f.severity.value in ("error", "warning")][:max_findings]
    lines = [f"- [{f.severity.value}] {f.element + ': ' if f.element else ''}{f.message}" for f in issues]
    return "\n".join([
        "The eFLINT specification below was generated from the regulatory text but failed "
        "validation. Fix every error (and the warnings where possible) and return the COMPLETE "
        "corrected specification. Keep correct parts unchanged.",
        "If a scenario expectation fails, decide from the legal text which side is wrong: fix the "
        "declarations when the text supports the expectation; change the expectation only if it "
        "misreads the text.",
        "",
        "FINDINGS:", *lines, "",
        "SPECIFICATION:", _dump(spec), "",
        "REGULATORY TEXT:", "<<<", text.strip(), ">>>",
    ])
