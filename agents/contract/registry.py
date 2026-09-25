"""Load the agent registry from ``config/agents.json`` and resolve intents to agents.

For ``in_process`` agents the registry needs the handler instances (they carry live
dependencies like an LLM provider), so callers pass a ``handlers`` mapping keyed by agent
name. ``http`` agents are built purely from the config entry (name + base_url + timeout).
"""
from __future__ import annotations

import json
from pathlib import Path

from .base import Agent, AgentHandler, HttpAgent, InProcessAgent


class RegistryError(RuntimeError):
    """Raised for malformed registry config or unresolved intents/agents."""


class AgentRegistry:
    def __init__(self, agents: dict[str, Agent], intent_map: dict[str, str]):
        self._agents = agents
        self._intent_map = intent_map

    @classmethod
    def from_config(
        cls,
        config_path: str | Path,
        *,
        handlers: dict[str, AgentHandler] | None = None,
    ) -> "AgentRegistry":
        handlers = handlers or {}
        spec = json.loads(Path(config_path).read_text(encoding="utf-8"))
        agents: dict[str, Agent] = {}
        intent_map: dict[str, str] = {}

        for entry in spec.get("agents", []):
            name = entry["name"]
            transport = entry.get("transport", "in_process")

            if transport == "in_process":
                handler = handlers.get(name)
                if handler is None:
                    # Registered but not wired in this process: skip rather than crash so
                    # a coordinator can run with a subset of agents available.
                    continue
                agents[name] = InProcessAgent(handler)
            elif transport == "http":
                base_url = entry.get("base_url")
                if not base_url:
                    raise RegistryError(f"Agent {name!r} uses http transport but has no base_url")
                agents[name] = HttpAgent(name, base_url, timeout_s=entry.get("timeout_s", 300))
            else:
                raise RegistryError(f"Unknown transport {transport!r} for agent {name!r}")

            for intent in entry.get("intents", []):
                intent_map[intent] = name

        return cls(agents, intent_map)

    def get(self, name: str) -> Agent:
        if name not in self._agents:
            raise RegistryError(f"No agent named {name!r} is available in this process")
        return self._agents[name]

    def get_for_intent(self, intent: str) -> Agent | None:
        name = self._intent_map.get(intent)
        return self._agents.get(name) if name else None

    def intents(self) -> list[str]:
        return sorted(self._intent_map)

    def names(self) -> list[str]:
        return sorted(self._agents)
