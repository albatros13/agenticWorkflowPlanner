"""Central configuration.

Secrets are read from the environment (optionally seeded from a local ``.env``)
and are **never** logged or embedded in prompts (guideline Step 18). Prompt and
pipeline versions live here so artifacts can be keyed by them (Step 17).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Bump these when behaviour changes so audit artifacts remain distinguishable.
PIPELINE_VERSION = "0.1.0"
PROMPT_VERSION = "2026-09-11"

REPO_ROOT = Path(__file__).resolve().parent.parent

# Keys that must never be echoed into logs or LLM prompts.
_SECRET_ENV_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "QDRANT_API_KEY",
        "REPLICATE_API_TOKEN",
        "NEO4J_PWD",
    }
)


def load_dotenv(path: Path | None = None, *, override: bool = False) -> None:
    """Seed ``os.environ`` from a ``.env`` file without a third-party dependency.

    Existing environment values win unless ``override`` is set. Values are not
    returned or logged.
    """
    path = path or (REPO_ROOT / ".env")
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if override or key not in os.environ:
            os.environ[key] = value


def redact(env_key: str, value: str | None) -> str:
    """Return a log-safe representation of a possibly-sensitive value."""
    if value is None:
        return "<unset>"
    if env_key in _SECRET_ENV_KEYS:
        return "<redacted>"
    return value


@dataclass(frozen=True)
class Settings:
    """Runtime configuration resolved from the environment."""

    llm_provider: str = "mock"  # "mock" | "anthropic" | "openai" | "local"
    anthropic_model: str = "claude-sonnet-4-6"
    openai_model: str = "gpt-4o"
    runs_dir: Path = field(default=REPO_ROOT / "runs")
    prompt_version: str = PROMPT_VERSION
    pipeline_version: str = PIPELINE_VERSION

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            llm_provider=os.getenv("LLM_PROVIDER", "mock"),
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            runs_dir=Path(os.getenv("RUNS_DIR", str(REPO_ROOT / "runs"))),
        )
