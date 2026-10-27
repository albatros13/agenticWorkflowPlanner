"""Environment-backed limits for the Petri net generator.

Every bound that protects the worker (input size, IR size, state-space exploration) lives here
so ops can tune it per deployment without code changes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Limits:
    max_input_bytes: int = 2_000_000          # text / BPMN XML / IR JSON payload size
    max_ir_nodes: int = 500                   # expressions in a process-algebra IR
    max_ir_depth: int = 25                    # nesting depth of the IR
    max_states: int = 20_000                  # reachability-graph states for the soundness check
    soundness_timeout_s: float = 5.0          # wall-clock budget for the soundness check
    max_inline_pnml_bytes: int = 1_000_000    # larger PNML is only returned by artifact reference

    @classmethod
    def from_env(cls) -> "Limits":
        return cls(
            max_input_bytes=_int("PETRI_NET_MAX_INPUT_BYTES", cls.max_input_bytes),
            max_ir_nodes=_int("PETRI_NET_MAX_IR_NODES", cls.max_ir_nodes),
            max_ir_depth=_int("PETRI_NET_MAX_IR_DEPTH", cls.max_ir_depth),
            max_states=_int("PETRI_NET_MAX_STATES", cls.max_states),
            soundness_timeout_s=_float("PETRI_NET_SOUNDNESS_TIMEOUT_S", cls.soundness_timeout_s),
            max_inline_pnml_bytes=_int("PETRI_NET_MAX_INLINE_PNML_BYTES", cls.max_inline_pnml_bytes),
        )
