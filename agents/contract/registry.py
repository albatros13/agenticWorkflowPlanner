"""Load the agent registry from ``config/agents.json`` and resolve intents to agents.

For ``in_process`` agents the registry needs the handler instances (they carry live
dependencies like an LLM provider), so callers pass a ``handlers`` mapping keyed by agent
name. ``http`` agents are built purely from the config entry (name + base_url + timeout).
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from .base import Agent, AgentError, AgentHandler, HttpAgent, InProcessAgent


class RegistryError(RuntimeError):
    """Raised for malformed registry config or unresolved intents/agents."""


class AgentCapability(BaseModel):
    """One intent that can be routed to an available agent, for discovery."""

    agent: str
    intent: str
    description: str = ""


class AgentRegistry:
    def __init__(
        self,
        agents: dict[str, Agent],
        intent_map: dict[str, str],
        descriptions: dict[str, str] | None = None,
    ):
        self._agents = agents
        self._intent_map = intent_map
        self._descriptions = descriptions or {}  # agent name -> config description

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
        descriptions: dict[str, str] = {}

        for entry in spec.get("agents", []):
            name = entry["name"]
            transport = entry.get("transport", "in_process")
            descriptions[name] = entry.get("description", "")

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

        return cls(agents, intent_map, descriptions)

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

    def capabilities(self) -> list[AgentCapability]:
        """The routable intents of the available agents, described by each agent's manifest.

        An agent whose manifest cannot be fetched (a remote agent that is down) keeps its intents,
        described by its config entry, so discovery never fails because of one agent.
        """
        found: list[AgentCapability] = []
        for name in self.names():
            try:
                described = {c.intent: c.description for c in self._agents[name].manifest().capabilities}
            except AgentError:
                described = {}
            found += [
                AgentCapability(agent=name, intent=intent,
                                description=described.get(intent) or self._descriptions.get(name, ""))
                for intent, owner in sorted(self._intent_map.items()) if owner == name
            ]
        return found
