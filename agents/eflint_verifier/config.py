"""Environment-backed settings for the eFLINT verifier wrapper.

The wrapped service is the external ``eflint-webservice`` (Rust, eflint-model-checking), which
listens on ``http://127.0.0.1:8100`` by default. Point the wrapper elsewhere with
``EFLINT_VERIFIER_URL``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


@dataclass(frozen=True)
class VerifierSettings:
    base_url: str = "http://127.0.0.1:8100"
    request_timeout_s: float = 30.0   # per HTTP call to the service
    poll_interval_s: float = 0.25     # first poll delay; doubles up to max_poll_interval_s
    max_poll_interval_s: float = 2.0
    wait_timeout_s: float = 300.0     # give up (and cancel the job) after this long
    default_max_states: int = 30      # the service's own default search bound

    @classmethod
    def from_env(cls) -> "VerifierSettings":
        return cls(
            base_url=os.getenv("EFLINT_VERIFIER_URL", cls.base_url),
            request_timeout_s=_float("EFLINT_VERIFIER_REQUEST_TIMEOUT_S", cls.request_timeout_s),
            poll_interval_s=_float("EFLINT_VERIFIER_POLL_INTERVAL_S", cls.poll_interval_s),
            max_poll_interval_s=_float("EFLINT_VERIFIER_MAX_POLL_INTERVAL_S", cls.max_poll_interval_s),
            wait_timeout_s=_float("EFLINT_VERIFIER_WAIT_TIMEOUT_S", cls.wait_timeout_s),
            default_max_states=_int("EFLINT_VERIFIER_MAX_STATES", cls.default_max_states),
        )
