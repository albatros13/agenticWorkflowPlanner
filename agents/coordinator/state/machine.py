"""A small, data-driven finite state machine for the project lifecycle.

The spec lives in ``config/project-states.json`` (see ``todo/PROJECT_STATE_MANAGEMENT.md``) so the
lifecycle can be revised without code changes. The machine answers three questions the
coordinator needs on every request:

* which actions are allowed in the current state (to *constrain the response*);
* what the next state is for a given action;
* how the global ``reviseDescription`` / ``deleteProject`` actions behave from any state.

Global actions are merged into every state's allowed set. ``reviseDescription`` is special:
if the revision changes workflow semantics it jumps to ``semanticsChangingTransition`` (and the
coordinator invalidates later approvals); otherwise the state is unchanged.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class StateMachineError(RuntimeError):
    """Raised for an unknown state, or an action not allowed in the current state."""


@dataclass(frozen=True)
class TransitionResult:
    """Outcome of applying an action."""

    from_state: str
    action: str
    to_state: str
    terminal: bool = False  # e.g. deleteProject: project is removed, not just moved
    invalidated: bool = False  # reviseDescription with semantic change reset later approvals


class StateMachine:
    def __init__(self, spec: dict):
        self._spec = spec
        self._states: dict[str, dict] = {s["id"]: s for s in spec.get("states", [])}
        if not self._states:
            raise StateMachineError("State machine spec has no states")
        self._global = spec.get("globalActions", {})
        # Convention: the first declared state is the initial state.
        self._initial = spec["states"][0]["id"]

    @classmethod
    def from_config(cls, path: str | Path) -> "StateMachine":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    # --- introspection ---------------------------------------------------------
    @property
    def initial_state(self) -> str:
        return self._initial

    def states(self) -> list[str]:
        return list(self._states)

    def _require_state(self, state: str) -> dict:
        if state not in self._states:
            raise StateMachineError(f"Unknown state {state!r}")
        return self._states[state]

    def allowed_actions(self, state: str) -> list[str]:
        """Per-state actions plus the always-available global actions (deduped, ordered)."""
        spec = self._require_state(state)
        actions = list(spec.get("allowedActions", []))
        for g in self._global:
            if g not in actions:
                actions.append(g)
        return actions

    def can(self, state: str, action: str) -> bool:
        return action in self.allowed_actions(state)

    def is_terminal(self, state: str) -> bool:
        spec = self._require_state(state)
        return not spec.get("transitions") and not any(
            a for a in spec.get("allowedActions", []) if a not in self._global
        )

    # --- transitions -----------------------------------------------------------
    def apply(self, state: str, action: str, *, semantics_changed: bool = False) -> TransitionResult:
        """Apply ``action`` to ``state`` and return the resulting :class:`TransitionResult`.

        ``semantics_changed`` only affects the global ``reviseDescription`` action.
        """
        self._require_state(state)
        if not self.can(state, action):
            raise StateMachineError(
                f"Action {action!r} is not allowed in state {state!r}. "
                f"Allowed: {', '.join(self.allowed_actions(state))}"
            )

        # Global actions take precedence over any per-state transition wording.
        if action == "deleteProject":
            return TransitionResult(state, action, state, terminal=True)

        if action == "reviseDescription":
            if semantics_changed:
                target = self._global.get("reviseDescription", {}).get(
                    "semanticsChangingTransition", state
                )
                return TransitionResult(state, action, target, invalidated=(target != state))
            return TransitionResult(state, action, state)

        # Otherwise, look up the per-state transition table.
        for t in self._states[state].get("transitions", []):
            if t["on"] == action:
                return TransitionResult(state, action, t["to"])

        # Allowed (e.g. a self-referential action) but no explicit transition -> stay put.
        return TransitionResult(state, action, state)
