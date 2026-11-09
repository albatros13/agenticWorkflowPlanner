"""Regression tests for the agent-side fixes in docs/review/CODE-REVIEW-FIX.md, plus tests for code
the review found untested (shared service endpoints, HttpAgent errors, eflint_verifier service)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agents.contract import AgentRequest
from agents.contract.base import AgentError, HttpAgent
from agents.contract.models import AgentManifest, AgentResult, Job, JobStatus
from agents.contract.service import JobTable, make_agent_app
from agents.regulatory_docs.config import VectorizationProfile
from agents.regulatory_docs.library import DocumentLibrary
from agents.regulatory_docs.vectorizer import vectorize_document
from agents.runs import is_run_id, runs_root
from agents.storage import safe_id
from agents.text_to_bpmn.pipeline.ingestion.markdown_loader import MarkdownLoader
from agents.text_to_bpmn.shaping import build_resources

from .regulatory_docs.conftest import InMemoryStore, offline_embedder_factory


# --- shared agent service (R1, P10) ----------------------------------------------------------------


class _Echo:
    def manifest(self):
        return AgentManifest(name="echo")

    def handle(self, request):
        ok = request.payload.get("ok", True)
        return AgentResult(agent="echo", intent=request.intent, output=request.payload,
                           status=JobStatus.succeeded if ok else JobStatus.failed, error=None if ok else "no")


def test_shared_service_serves_the_contract_and_reads_the_handler_per_request():
    handler = {"h": _Echo()}
    client = TestClient(make_agent_app("echo", lambda: handler["h"]))
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/agent/manifest").json()["name"] == "echo"

    job = client.post("/agent/invoke", json={"intent": "x", "payload": {"ok": False}}).json()
    assert job["status"] == "failed" and job["error"] == "no"
    assert client.get(f"/agent/jobs/{job['job_id']}").json()["result"]["output"] == {"ok": False}
    assert client.get("/agent/jobs/nope").json()["error"] == "unknown job"

    class Other(_Echo):
        def manifest(self):
            return AgentManifest(name="other")

    handler["h"] = Other()  # a test may swap the module's handler after the app is built
    assert client.get("/agent/manifest").json()["name"] == "other"


def test_job_table_is_bounded():
    table = JobTable(max_jobs=2)
    for i in range(3):
        table.add(Job(job_id=str(i)))
    assert len(table) == 2 and table.get("0").error == "unknown job" and table.get("2").job_id == "2"


# --- HttpAgent error paths (previously untested) ---------------------------------------------------


class _BrokenClient:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, *a, **k):
        raise ConnectionError("refused")

    post = get


def test_http_agent_normalises_transport_errors(monkeypatch):
    agent = HttpAgent("remote", "http://nowhere/")
    monkeypatch.setattr(agent, "_client", lambda: _BrokenClient())
    with pytest.raises(AgentError, match="Failed to fetch manifest from http://nowhere"):
        agent.manifest()
    result = agent.invoke(AgentRequest(intent="x"))
    assert result.status == JobStatus.failed and "ConnectionError: refused" in result.error


# --- ids and runs (B5, R3) --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["abc", "u@x.com", "a.b-c_d+e", "0123456789ab"])
def test_safe_ids_pass(value):
    assert safe_id(value) == value


@pytest.mark.parametrize("value", ["", ".", "..", "a/b", "a\\b", "a..b", ".env", None, "x" * 129])
def test_unsafe_ids_fail(value):
    with pytest.raises(ValueError):
        safe_id(value)


def test_runs_root_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("X_RUNS", str(tmp_path / "env"))
    assert runs_root("text_to_bpmn", "X_RUNS", tmp_path / "arg") == tmp_path / "arg"
    assert runs_root("text_to_bpmn", "X_RUNS") == tmp_path / "env"
    monkeypatch.delenv("X_RUNS")
    assert runs_root("text_to_bpmn", "X_RUNS").name == "runs"
    assert is_run_id("abc123") and not is_run_id("../x") and not is_run_id(None)


# --- regulatory docs (B12, P4, library ids) ----------------------------------------------------------


def _profile(max_chars: int) -> VectorizationProfile:
    return VectorizationProfile.model_validate({
        "embedding": {"provider": "hash", "model": "hash", "dimension": 16},
        "chunking": {"max_chars": max_chars},
        "qdrant": {"cluster": "memory", "collection": "c"},
    })


def test_revectorizing_with_other_chunking_leaves_no_stale_chunks():
    text = "# A\n\n" + "\n\n".join(f"Paragraph {i} " + "word " * 40 for i in range(6))
    doc = MarkdownLoader().load_text(text, source_document="d.md", doc_id="0123456789ab")
    store, embed = InMemoryStore(), offline_embedder_factory(_profile(200).embedding)
    small = vectorize_document(doc, _profile(200), profile_name="p", embedder=embed, store=store)
    large = vectorize_document(doc, _profile(5000), profile_name="p", embedder=embed, store=store)
    assert small.chunks > large.chunks
    assert store.count() == large.chunks  # the extra chunks of the first run are gone


def test_library_ids_and_unsafe_doc_ids(tmp_path):
    lib = DocumentLibrary(tmp_path / "docs")
    record = lib.add_from_text("Article 1. Controllers must keep records.", title="Rules")
    assert lib.ids() == {record.doc_id}
    for bad in ("../x", "..", "deadbeef", "/etc"):
        with pytest.raises(KeyError):
            lib.get(bad)
        with pytest.raises(KeyError):
            lib.load_document(bad)


def test_regulatory_agent_reuses_clients_per_profile(tmp_path):
    from agents.regulatory_docs.agent import SEARCH_VECTORS, RegulatoryDocsHandler

    built = {"embedders": 0, "stores": 0}
    store = InMemoryStore()

    def embedder_factory(cfg):
        built["embedders"] += 1
        return offline_embedder_factory(cfg)

    def store_factory(cfg):
        built["stores"] += 1
        return store

    handler = RegulatoryDocsHandler(library=DocumentLibrary(tmp_path / "docs"),
                                    embedder_factory=embedder_factory, store_factory=store_factory)
    for _ in range(3):
        assert handler.handle(AgentRequest(intent=SEARCH_VECTORS, payload={"query": "records"})).ok
    assert built == {"embedders": 1, "stores": 1}


def test_demo_upload_is_size_limited(tmp_path, monkeypatch):
    import agents.regulatory_docs.demo.main as demo

    monkeypatch.setenv("REGDOCS_DIR", str(tmp_path / "docs"))
    monkeypatch.setattr(demo, "MAX_UPLOAD_BYTES", 10)
    resp = TestClient(demo.app).post("/api/upload_file", files={"file": ("big.md", b"x" * 11, "text/markdown")})
    assert resp.status_code == 413


# --- text_to_bpmn shaping (B16) -------------------------------------------------------------------


def test_threshold_upper_bound_of_zero_is_kept():
    threshold = SimpleNamespace(measure="delta", operator="between", value=-5, value_high=0, unit="")
    element = SimpleNamespace(id="e", type=SimpleNamespace(value="task"), name="n", actor=None,
                              status=SimpleNamespace(value="explicit"), gateway_kind=None,
                              thresholds=[threshold], temporal_constraints=[], evidence_refs=[])
    process = SimpleNamespace(actors=[], elements=[element], relations=[])
    model = SimpleNamespace(graph=SimpleNamespace(decisions=[], nodes=[], flows=[]))
    out = build_resources(SimpleNamespace(items=[]), process, [], model)
    assert out["elements"][0]["thresholds"] == ["delta between -5..0"]


# --- text_to_eflint (B13, B17) ---------------------------------------------------------------------


def test_repair_rounds_are_clamped():
    from agents.text_to_eflint.agent import MAX_REPAIR_ROUNDS, _bounded_int

    assert _bounded_int(999, default=2, low=0, high=MAX_REPAIR_ROUNDS) == MAX_REPAIR_ROUNDS
    assert _bounded_int(-3, default=2, low=0, high=MAX_REPAIR_ROUNDS) == 0
    assert _bounded_int(None, default=2, low=0, high=MAX_REPAIR_ROUNDS) == 2
    with pytest.raises(ValueError):
        _bounded_int("lots", default=2, low=0, high=MAX_REPAIR_ROUNDS)


def test_quantifier_bindings_are_bounded(monkeypatch):
    from agents.text_to_eflint.generation import simulator as S
    from agents.text_to_eflint.generation.syntax import parse_program

    prog = parse_program('Fact person Identified by String.\n+person("a").\n+person("b").\n'
                         '?(Exists person : person == "a").\n', source="t")
    sim = S.Simulator.from_decls(prog.decls)
    for st in prog.statements:
        if st.kind == "assert":
            sim.postulate(st.term)
    query = prog.statements[-1].expr
    assert sim.query(query) is True
    monkeypatch.setattr(S, "MAX_QUANTIFIER_BINDINGS", 1)
    with pytest.raises(S.EvalError, match="2 bindings"):
        sim.query(query)


# --- eflint_verifier service (was 0 % covered) -------------------------------------------------------


def test_eflint_verifier_service_verify_endpoint(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    import agents.eflint_verifier.service as svc

    from .eflint_verifier._fake_service import NORMS, PROCESS_PN, RULES, FakeService, make_handler

    body = {"physical": {"language": "petri-net", "source": PROCESS_PN},
            "normative": {"language": "eflint", "source": NORMS}, "qualification": RULES}
    monkeypatch.setattr(svc, "_handler", make_handler(FakeService(), tmp_path))
    client = TestClient(svc.app)
    assert client.get("/agent/manifest").json()["name"] == "eflint_verifier"
    ok = client.post("/verify", json=body)
    assert ok.status_code == 200 and ok.json()["verdict"] == "passed"

    monkeypatch.setattr(svc, "_handler", make_handler(
        FakeService(fail={"code": "solver_error", "message": "boom"}), tmp_path))
    bad = client.post("/verify", json=body)
    assert bad.status_code == 502 and "boom" in bad.json()["detail"]
    assert json.loads(bad.content)["job_status"] == "failed"
