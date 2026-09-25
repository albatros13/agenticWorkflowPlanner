"""Data-driven project lifecycle state machine (loads ``config/project-states.json``)."""
from .machine import StateMachine, StateMachineError, TransitionResult

__all__ = ["StateMachine", "StateMachineError", "TransitionResult"]
