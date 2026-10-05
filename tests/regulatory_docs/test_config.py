"""Vectorization config loads from JSON and resolves profiles."""
import json

import pytest

from agents.paths import CONFIG_DIR
from agents.regulatory_docs.config import VectorizationConfig


def test_shipped_config_has_a_resolvable_default():
    cfg = VectorizationConfig.load(CONFIG_DIR / "vectorization.json")
    assert cfg.default_profile in cfg.profiles
    default = cfg.profile()  # no name -> default
    assert default.embedding.dimension > 0
    assert default.qdrant.collection


def test_profile_lookup_and_unknown_raises(tmp_path):
    cfg_path = tmp_path / "vectorization.json"
    cfg_path.write_text(json.dumps({
        "$comment": "ignored",
        "default_profile": "a",
        "profiles": {
            "a": {"embedding": {"provider": "hash", "dimension": 8}},
            "b": {"embedding": {"provider": "openai", "model": "text-embedding-3-small", "dimension": 1536}},
        },
    }))
    cfg = VectorizationConfig.load(cfg_path)
    assert cfg.profile_names() == ["a", "b"]
    assert cfg.profile("b").embedding.provider == "openai"
    with pytest.raises(KeyError):
        cfg.profile("missing")


def test_env_override(monkeypatch, tmp_path):
    cfg_path = tmp_path / "custom.json"
    cfg_path.write_text(json.dumps({"default_profile": "x", "profiles": {"x": {}}}))
    monkeypatch.setenv("VECTORIZATION_CONFIG", str(cfg_path))
    # Re-import module-level constant through a fresh load call path.
    import importlib
    import agents.regulatory_docs.config as config_mod
    importlib.reload(config_mod)
    cfg = config_mod.VectorizationConfig.load()
    assert cfg.default_profile == "x"
    importlib.reload(config_mod)  # restore for other tests


def test_summaries_flag_default():
    cfg = VectorizationConfig.load(CONFIG_DIR / "vectorization.json")
    summaries = cfg.summaries()
    assert any(s["is_default"] for s in summaries)
    assert {"name", "provider", "model", "dimension", "cluster", "collection"} <= summaries[0].keys()
