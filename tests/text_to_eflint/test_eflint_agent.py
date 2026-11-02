"""The text_to_eflint agent handler, HTTP service and demo endpoint."""
import json

import pytest
from fastapi.testclient import TestClient

import agents.text_to_eflint.demo.main as demo
from agents.contract import AgentRequest
from agents.text_to_bpmn.pipeline.llm.mock import ScriptedLLMProvider
from agents.text_to_eflint.agent import GENERATE_EFLINT, VALIDATE_EFLINT, TextToEflintHandler
from agents.text_to_eflint.generation.prompts import TASK_DRAFT

from ._eflint_samples import TEXT, spec


@pytest.fixture(autouse=True)
def _runs_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("EFLINT_RUNS_DIR", str(tmp_path / "runs"))


def _handler():
    return TextToEflintHandler(provider=ScriptedLLMProvider({TASK_DRAFT: spec()}))


def test_manifest_lists_both_intents():
    m = TextToEflintHandler().manifest()
    assert m.serves(GENERATE_EFLINT) and m.serves(VALIDATE_EFLINT)


def test_generate_saves_run_artifacts(tmp_path):
    res = _handler().handle(AgentRequest(intent=GENERATE_EFLINT, payload={"text": TEXT, "title": "Consent"}))
    assert res.ok, res.error
    assert res.output["status"] == "succeeded" and res.output["provider"] == "mock"
    run = tmp_path / "runs" / res.artifacts_ref
    assert (run / "Norms.eflint").read_text().startswith('#require "Ontology.eflint".')
    report = json.loads((run / "report.json").read_text())
    assert report["summary"]["scenarios_passed"] == 1 and "files" not in report
    assert json.loads((run / "spec.json").read_text())["title"] == "GDPR consent"


def test_generate_from_regulatory_docs_document_sections(tmp_path):
    from agents.regulatory_docs.library import DocumentLibrary

    lib = DocumentLibrary(tmp_path / "docs")
    rec = lib.add_from_text("# Article 6\n\nConsent text here shall apply.\n\n# Article 99\n\nUnrelated.\n", title="gdpr.md")
    provider = ScriptedLLMProvider({TASK_DRAFT: spec()})
    handler = TextToEflintHandler(provider=provider, documents_dir=tmp_path / "docs")
    res = handler.handle(AgentRequest(intent=GENERATE_EFLINT, payload={"document_id": rec.doc_id, "sections": ["article 6"]}))
    assert res.ok, res.error
    assert "Consent text here" in provider.calls[0].prompt and "Unrelated" not in provider.calls[0].prompt


def test_generate_errors_are_failed_results():
    h = _handler()
    assert h.handle(AgentRequest(intent=GENERATE_EFLINT, payload={})).error.startswith("payload.text")
    bad_base = h.handle(AgentRequest(intent=GENERATE_EFLINT, payload={"text": TEXT, "base_spec": "Event e."}))
    assert not bad_base.ok and "unsupported" in bad_base.error
    assert not h.handle(AgentRequest(intent="nope")).ok


def test_validate_spec_json_and_eflint_text():
    h = TextToEflintHandler()
    res = h.handle(AgentRequest(intent=VALIDATE_EFLINT, payload={"spec": spec(), "source_text": TEXT}))
    assert res.ok and res.output["status"] == "succeeded"

    eflint = ("Fact person Identified by String.\nFact member Identified by person.\n"
              "Act join Actor person Holds when !member() Creates member().\n+person(\"ann\").")
    scenario = '#require "base.eflint".\njoin("ann").\n?Holds(member("ann")).\n?!Enabled(join("ann")).\n?Holds(member("bob")).'
    res = h.handle(AgentRequest(intent=VALIDATE_EFLINT, payload={"eflint": eflint, "scenarios": {"s1": scenario}}))
    assert res.ok, res.error
    out = res.output
    assert out["status"] == "needs_review"  # member("bob") does not hold
    [sc] = out["scenarios"]
    assert [s["ok"] for s in sc["steps"]] == [True, True, True, False]


def test_service_generate_and_validate(monkeypatch):
    import agents.text_to_eflint.service as service

    monkeypatch.setattr(service, "_handler", _handler())
    client = TestClient(service.app)
    assert client.get("/agent/manifest").json()["name"] == "text_to_eflint"
    r = client.post("/generate", json={"text": TEXT})
    assert r.status_code == 200 and r.json()["status"] == "succeeded"
    assert client.post("/validate", json={}).status_code == 422


def test_demo_generate_endpoint(monkeypatch):
    monkeypatch.setattr(demo, "_select_provider", lambda settings: ScriptedLLMProvider({TASK_DRAFT: spec()}))
    r = TestClient(demo.app).post("/api/generate", json={"text": TEXT, "domain_context": "newsletter"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["summary"]["errors"] == 0 and "spec.eflint" in data["files"]
