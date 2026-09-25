"""The api-backed LLM providers delegate the real SDK call to the ``api/`` package.

No network: we monkeypatch the ``api`` structured functions and assert the pipeline
providers forward the right arguments and wrap the result in an ``LLMResult``. This
locks in that ``api/`` is the single call site for provider SDKs.
"""
import pytest

from agents.text_to_bpmn.pipeline.config import Settings
from agents.text_to_bpmn.pipeline.llm.anthropic_provider import AnthropicProvider
from agents.text_to_bpmn.pipeline.llm.base import LLMError
from agents.text_to_bpmn.pipeline.llm.factory import get_provider
from agents.text_to_bpmn.pipeline.llm.openai_provider import OpenAIProvider

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def test_anthropic_provider_delegates_to_api(monkeypatch):
    seen = {}

    def fake(**kwargs):
        seen.update(kwargs)
        return {"ok": True}, '{"ok": true}', "claude-test"

    import api.anthropic_client as client
    monkeypatch.setattr(client, "ask_anthropic_structured", fake)

    result = AnthropicProvider(model="claude-test").generate_json(
        task="demo", system="sys", prompt="hi", schema=SCHEMA, max_tokens=99,
    )
    assert result.data == {"ok": True}
    assert result.provider == "anthropic" and result.model == "claude-test" and result.task == "demo"
    # Arguments are forwarded verbatim to the api layer.
    assert seen == {
        "task": "demo", "system": "sys", "prompt": "hi",
        "schema": SCHEMA, "max_tokens": 99, "model": "claude-test",
    }


def test_openai_provider_delegates_to_api(monkeypatch):
    pytest.importorskip("openai", reason="requires the 'llm' extra (openai)")

    def fake(**kwargs):
        return {"ok": False}, '{"ok": false}', kwargs["model"]

    import api.openai_client as client
    monkeypatch.setattr(client, "ask_openai_structured", fake)

    result = OpenAIProvider(model="gpt-test").generate_json(
        task="demo", system="s", prompt="p", schema=SCHEMA,
    )
    assert result.data == {"ok": False}
    assert result.provider == "openai" and result.model == "gpt-test"


def test_provider_without_sdk_raises_llm_error(monkeypatch):
    # When the provider SDK isn't installed, the lazy api import fails -> LLMError,
    # not a raw ModuleNotFoundError leaking out of the pipeline.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "api.openai_client" or name.startswith("openai"):
            raise ModuleNotFoundError("No module named 'openai'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(LLMError, match="openai.*not installed"):
        OpenAIProvider(model="gpt-test").generate_json(
            task="t", system="s", prompt="p", schema=SCHEMA,
        )


def test_provider_wraps_api_errors_as_llm_error(monkeypatch):
    import api.anthropic_client as client

    def boom(**kwargs):
        raise RuntimeError("no key")

    monkeypatch.setattr(client, "ask_anthropic_structured", boom)
    with pytest.raises(LLMError, match="Anthropic call failed: no key"):
        AnthropicProvider().generate_json(task="t", system="s", prompt="p", schema=SCHEMA)


def test_factory_auto_selects_by_api_key(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = get_provider(Settings(llm_provider="auto"))
    assert provider.name == "anthropic"

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert get_provider(Settings(llm_provider="auto")).name == "openai"


def test_factory_auto_without_keys_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="No LLM configured"):
        get_provider(Settings(llm_provider="auto"))
