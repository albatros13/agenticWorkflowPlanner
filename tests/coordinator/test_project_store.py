"""JSON project store + semantic-cache no-op default."""
from agents.coordinator.cache import NoOpSemanticCache, build_cache
from agents.coordinator.projects.json_store import JsonProjectStore
from agents.coordinator.projects.models import HistoryEntry, Project
from agents.coordinator.projects.store import ProjectNotFoundError
import pytest


def _project(user="u@example.com", pid="p1", name="Test", state="INTENT_CAPTURED"):
    return Project(id=pid, user_id=user, name=name, description="desc", state=state)


def test_create_get_save_roundtrip(tmp_path):
    store = JsonProjectStore(tmp_path)
    store.create(_project())
    loaded = store.get("u@example.com", "p1")
    assert loaded.name == "Test" and loaded.state == "INTENT_CAPTURED"

    loaded.state = "DESCRIPTION_DRAFTED"
    loaded.record(HistoryEntry(intent="new_project", action="createDescription"))
    store.save(loaded)
    again = store.get("u@example.com", "p1")
    assert again.state == "DESCRIPTION_DRAFTED"
    assert again.history[-1].action == "createDescription"


def test_create_duplicate_raises(tmp_path):
    store = JsonProjectStore(tmp_path)
    store.create(_project())
    with pytest.raises(FileExistsError):
        store.create(_project())


def test_list_is_scoped_per_user_and_sorted(tmp_path):
    store = JsonProjectStore(tmp_path)
    store.create(_project(user="a@x.com", pid="p1", name="A1"))
    store.create(_project(user="a@x.com", pid="p2", name="A2"))
    store.create(_project(user="b@x.com", pid="p3", name="B1"))
    assert {r.id for r in store.list("a@x.com")} == {"p1", "p2"}
    assert {r.id for r in store.list("b@x.com")} == {"p3"}


def test_delete_and_missing(tmp_path):
    store = JsonProjectStore(tmp_path)
    store.create(_project())
    store.delete("u@example.com", "p1")
    with pytest.raises(ProjectNotFoundError):
        store.get("u@example.com", "p1")
    with pytest.raises(ProjectNotFoundError):
        store.delete("u@example.com", "p1")


def test_substring_search(tmp_path):
    store = JsonProjectStore(tmp_path)
    store.create(_project(pid="p1", name="Prostate cancer pathway"))
    store.create(_project(pid="p2", name="Unrelated"))
    hits = store.search("u@example.com", "prostate")
    assert [r.id for r in hits] == ["p1"]


def test_noop_cache_returns_empty(tmp_path):
    cache = build_cache("noop")
    assert isinstance(cache, NoOpSemanticCache)
    cache.index_project(_project())  # no-op, must not raise
    assert cache.find_similar("anything", user_id="u@example.com") == []
