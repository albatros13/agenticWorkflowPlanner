"""Process-algebra intermediate representation (IR) for the text -> Petri net path.

The LLM produces this small, typed, versioned tree (activity / sequence / xor / parallel / loop)
instead of places and transitions; :mod:`.compiler` then turns it into a net deterministically.
Pydantic enforces the shape; :func:`check_ir` adds the semantic checks (unique IDs, size and
depth bounds) that a schema alone cannot express.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from .schemas import Finding, Severity

SCHEMA_VERSION = "1.0"

_ID_PATTERN = r"^[A-Za-z_][A-Za-z0-9_.\-]*$"


class Activity(BaseModel):
    """One atomic task -> one visible transition."""

    kind: Literal["activity"] = "activity"
    id: str = Field(pattern=_ID_PATTERN, description="Stable identifier, unique in the process.")
    label: str = Field(min_length=1, description="Human-readable task name, worded as in the source.")


class Sequence(BaseModel):
    """Run ``steps`` one after another."""

    kind: Literal["sequence"] = "sequence"
    steps: list["Expr"] = Field(min_length=1)


class Branch(BaseModel):
    """One alternative of an exclusive choice. ``body=None`` means "do nothing" (skip)."""

    condition: str | None = Field(default=None, description="Condition as stated in the source, if any.")
    body: "Expr | None" = None


class Xor(BaseModel):
    """Exclusive choice: exactly one branch runs."""

    kind: Literal["xor"] = "xor"
    branches: list[Branch] = Field(min_length=2)


class Parallel(BaseModel):
    """AND split/join: all branches run concurrently and synchronize before continuing."""

    kind: Literal["parallel"] = "parallel"
    branches: list["Expr"] = Field(min_length=2)


class Loop(BaseModel):
    """Structured repeat: run ``body``; then either exit, or run ``redo`` (if any) and repeat."""

    kind: Literal["loop"] = "loop"
    body: "Expr"
    redo: "Expr | None" = None
    exit_condition: str | None = None
    continue_condition: str | None = None


Expr = Annotated[Union[Activity, Sequence, Xor, Parallel, Loop], Field(discriminator="kind")]


class Ambiguity(BaseModel):
    """Something the source text leaves open. ``blocking`` ones stop generation."""

    severity: Literal["blocking", "non_blocking"]
    message: str
    question: str = Field(description="A targeted question the coordinator can ask the user.")
    element_ids: list[str] = Field(default_factory=list)


class ProcessIR(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    process_name: str = ""
    description: str = ""
    root: Expr
    assumptions: list[str] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    @property
    def blocking_ambiguities(self) -> list[Ambiguity]:
        return [a for a in self.ambiguities if a.severity == "blocking"]


for _model in (Sequence, Branch, Xor, Parallel, Loop, ProcessIR):
    _model.model_rebuild()


def check_ir(ir: ProcessIR, *, max_nodes: int, max_depth: int) -> list[Finding]:
    """Deterministic semantic checks run before compilation. Errors block compilation."""
    findings: list[Finding] = []
    seen: dict[str, int] = {}
    count = 0
    deepest = 0

    def walk(expr, depth: int) -> None:
        nonlocal count, deepest
        count += 1
        deepest = max(deepest, depth)
        if isinstance(expr, Activity):
            seen[expr.id] = seen.get(expr.id, 0) + 1
        elif isinstance(expr, Sequence):
            for step in expr.steps:
                walk(step, depth + 1)
        elif isinstance(expr, Xor):
            skips = sum(1 for b in expr.branches if b.body is None)
            if skips == len(expr.branches):
                findings.append(Finding(severity=Severity.error, code="xor_all_branches_empty",
                                        message="An exclusive choice has no branch with any activity."))
            elif skips > 1:
                findings.append(Finding(severity=Severity.warning, code="xor_multiple_skip_branches",
                                        message="An exclusive choice has several empty (skip) branches."))
            for branch in expr.branches:
                if branch.body is not None:
                    walk(branch.body, depth + 1)
        elif isinstance(expr, Parallel):
            for branch in expr.branches:
                walk(branch, depth + 1)
        elif isinstance(expr, Loop):
            walk(expr.body, depth + 1)
            if expr.redo is not None:
                walk(expr.redo, depth + 1)
            if not expr.exit_condition:
                findings.append(Finding(severity=Severity.warning, code="loop_without_exit_condition",
                                        message="A loop has no stated exit condition; termination is "
                                                "modelled as a free choice."))

    walk(ir.root, 1)

    dupes = sorted(i for i, n in seen.items() if n > 1)
    if dupes:
        findings.append(Finding(severity=Severity.error, code="duplicate_activity_id",
                                message=f"Activity IDs must be unique: {', '.join(dupes)}.",
                                element_ids=dupes))
    if count > max_nodes:
        findings.append(Finding(severity=Severity.error, code="ir_too_large",
                                message=f"IR has {count} expressions (limit {max_nodes})."))
    if deepest > max_depth:
        findings.append(Finding(severity=Severity.error, code="ir_too_deep",
                                message=f"IR nesting depth {deepest} exceeds limit {max_depth}."))
    return findings

