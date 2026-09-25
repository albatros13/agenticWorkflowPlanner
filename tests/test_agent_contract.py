"""Agent contract: InProcessAgent, registry resolution, and manifest discovery."""
import json

from agents.contract import (
    AgentHandler,
    AgentManifest,
    AgentRequest,
    AgentResult,
    AgentRegistry,
    Capability,
    InProcessAgent,
)
from agents.contract.models import JobStatus
from agents.contract.registry import RegistryError
import pytest


class EchoHandler(AgentHandler):
    def manifest(self) -> AgentManifest:
        return AgentManifest(
            name="echo",
            capabilities=[Capability(intent="echo")],
        )

    def handle(self, request: AgentRequest) -> AgentResult:
        return AgentResult(agent="echo", intent=request.intent, output={"echo": request.payload})


def test_in_process_agent_invokes_handler():
    agent = InProcessAgent(EchoHandler())
    assert agent.name == "echo"
    assert agent.manifest().serves("echo")
    result = agent.invoke(AgentRequest(intent="echo", payload={"x": 1}))
    assert result.ok and result.output == {"echo": {"x": 1}}


def test_registry_resolves_intent_to_in_process_agent(tmp_path):
    cfg = tmp_path / "agents.json"
    cfg.write_text(json.dumps({"agents": [
        {"name": "echo", "transport": "in_process", "intents": ["echo", "repeat"]}
    ]}))
    reg = AgentRegistry.from_config(cfg, handlers={"echo": EchoHandler()})
    assert reg.get_for_intent("echo").name == "echo"
    assert reg.get_for_intent("repeat").name == "echo"
    assert reg.get_for_intent("missing") is None
    assert reg.names() == ["echo"]


def test_registry_skips_in_process_agent_without_handler(tmp_path):
    cfg = tmp_path / "agents.json"
    cfg.write_text(json.dumps({"agents": [
        {"name": "echo", "transport": "in_process", "intents": ["echo"]}
    ]}))
    reg = AgentRegistry.from_config(cfg, handlers={})  # no handler wired
    assert reg.get_for_intent("echo") is None


def test_registry_http_agent_requires_base_url(tmp_path):
    cfg = tmp_path / "agents.json"
    cfg.write_text(json.dumps({"agents": [
        {"name": "remote", "transport": "http", "intents": ["x"]}
    ]}))
    with pytest.raises(RegistryError):
        AgentRegistry.from_config(cfg)


def test_registry_builds_http_agent(tmp_path):
    cfg = tmp_path / "agents.json"
    cfg.write_text(json.dumps({"agents": [
        {"name": "remote", "transport": "http", "base_url": "http://localhost:9999", "intents": ["x"]}
    ]}))
    reg = AgentRegistry.from_config(cfg)
    agent = reg.get_for_intent("x")
    assert agent.name == "remote" and agent.base_url == "http://localhost:9999"


def test_default_config_wires_text_to_bpmn():
    """The shipped config/agents.json resolves engineer_process to text_to_bpmn."""
    from agents.paths import AGENTS_CONFIG
    from agents.text_to_bpmn.agent import TextToBpmnHandler

    reg = AgentRegistry.from_config(AGENTS_CONFIG, handlers={"text_to_bpmn": TextToBpmnHandler()})
    assert reg.get_for_intent("engineer_process").name == "text_to_bpmn"
