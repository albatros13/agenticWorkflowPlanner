"""The structured eFLINT specification the LLM produces (and the renderer turns into ``.eflint``).

The model emits *declarations as fields* and *expressions/terms as eFLINT strings*: the overall
structure is enforced by the JSON schema (``prompts.SPEC_SCHEMA``), while conditions stay in the
syntax the model already knows and are checked by :mod:`.syntax` / :mod:`.checks`. The layout
mirrors the reference data-governance framework: atomic types, legal-condition facts, duties,
acts, domain instances, initial state, scenarios.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

ATOMIC_BASES = ("String", "Int")


class Severity(str, Enum):
    error = "error"
    warning = "warning"
    info = "info"


class Finding(BaseModel):
    severity: Severity
    code: str
    message: str
    element: str = ""  # declaration name or "scenario <n> step <i>"


class Grounded(BaseModel):
    """Traceability back to the regulatory text."""

    source: str = ""        # citation, e.g. "GDPR Art. 5(1)(b)"; "domain model" for non-legal state
    source_quote: str = ""  # verbatim span copied from the input text
    explanation: str = ""   # short label, rendered into the comment above the declaration


class FactDecl(Grounded):
    name: str
    identified_by: list[str] = Field(default_factory=lambda: ["String"])  # ["String"]/["Int"] or type names
    holds_when: str | None = None

    @property
    def atomic(self) -> bool:
        return len(self.identified_by) == 1 and self.identified_by[0] in ATOMIC_BASES


class ActDecl(Grounded):
    name: str
    actor: str
    recipient: str | None = None
    related_to: list[str] = Field(default_factory=list)
    holds_when: str | None = None
    creates: list[str] = Field(default_factory=list)
    terminates: list[str] = Field(default_factory=list)

    @property
    def params(self) -> list[str]:
        """Instance argument order: actor, recipient, related_to (as in eFLINT)."""
        return [self.actor, *([self.recipient] if self.recipient else []), *self.related_to]


class DutyDecl(Grounded):
    name: str
    holder: str
    claimant: str
    related_to: list[str] = Field(default_factory=list)
    violated_when: str | None = None

    @property
    def params(self) -> list[str]:
        return [self.holder, self.claimant, *self.related_to]


class Statement(BaseModel):
    term: str          # a ground term, e.g. controller("company-a")
    comment: str = ""


class ScenarioStep(BaseModel):
    kind: Literal["assert", "retract", "perform", "query"]
    term: str
    query: Literal["holds", "enabled", "violated"] = "holds"  # only for kind == "query"
    expect: bool = True  # query: expected truth; perform: expected to be enabled
    comment: str = ""


class Scenario(BaseModel):
    name: str
    description: str = ""
    steps: list[ScenarioStep] = Field(default_factory=list)


class EflintSpec(BaseModel):
    title: str = ""
    facts: list[FactDecl] = Field(default_factory=list)
    duties: list[DutyDecl] = Field(default_factory=list)
    acts: list[ActDecl] = Field(default_factory=list)
    instances: list[Statement] = Field(default_factory=list)      # +atomic("..") domain instances
    initial_state: list[Statement] = Field(default_factory=list)  # +composite(..) initial facts
    scenarios: list[Scenario] = Field(default_factory=list)

    def declaration_names(self) -> list[str]:
        return [d.name for d in (*self.facts, *self.duties, *self.acts)]
