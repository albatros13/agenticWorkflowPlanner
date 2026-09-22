"""Demo API tests. The LLM is mocked (offline) by overriding provider selection."""
import app.main as api
from fastapi.testclient import TestClient

from src.ingestion.chunker import SemanticChunker
from src.ingestion.markdown_loader import MarkdownLoader
from src.llm.mock import ScriptedLLMProvider

TEXT = (
    "# Follow-up\n\n"
    "The family physician assesses the IPSS. If improvement is at least 25%, "
    "continue treatment. If improvement is less than 25%, refer back to the urologist.\n"
)


def _provider():
    doc = MarkdownLoader().load_text(TEXT, source_document="input.md", doc_id="input")
    chunk = SemanticChunker().chunk(doc)[0]
    return ScriptedLLMProvider({
        "evidence_extraction": {"items": [
            {"id": "E1", "text": "Assess IPSS.", "source_chunk_id": chunk.id},
            {"id": "E2", "text": "If IPSS improves >= 25%, continue.", "source_chunk_id": chunk.id},
            {"id": "E3", "text": "If < 25%, refer back.", "source_chunk_id": chunk.id},
        ]},
        "logic_interpretation": {
            "name": "Follow-up", "actors": ["Family physician"],
            "elements": [
                {"id": "S", "type": "Start", "name": "At follow-up"},
                {"id": "A1", "type": "Activity", "actor": "Family physician",
                 "name": "Assess IPSS", "evidence_refs": ["E1"]},
                # gateway_kind intentionally omitted -> normalized to XOR
                {"id": "D1", "type": "Decision", "name": "Improved >= 25%?",
                 "evidence_refs": ["E2", "E3"],
                 "thresholds": [{"measure": "IPSS improvement", "operator": ">=", "value": 25, "unit": "%"}]},
                {"id": "A2", "type": "Activity", "actor": "Family physician",
                 "name": "Continue treatment", "evidence_refs": ["E2"]},
                {"id": "A3", "type": "Activity", "actor": "Family physician",
                 "name": "Refer back to urologist", "evidence_refs": ["E3"]},
                {"id": "E", "type": "End", "name": "End"},
            ],
            "relations": [
                {"id": "r1", "type": "sequence", "source": "S", "target": "A1"},
                {"id": "r2", "type": "sequence", "source": "A1", "target": "D1"},
                {"id": "r3", "type": "conditional", "source": "D1", "target": "A2", "condition": ">= 25%"},
                {"id": "r4", "type": "conditional", "source": "D1", "target": "A3", "condition": "< 25%"},
                {"id": "r5", "type": "sequence", "source": "A2", "target": "E"},
                {"id": "r6", "type": "sequence", "source": "A3", "target": "E"},
            ],
        },
    })


def test_generate_returns_bpmn_and_resources(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_select_provider", lambda settings: _provider())
    monkeypatch.setenv("RUNS_DIR", str(tmp_path))
    client = TestClient(api.app)

    resp = client.post("/api/generate", json={"text": TEXT, "process_name": "Follow-up"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["bpmn_xml"].lstrip().startswith("<?xml")
    assert data["validation"]["bpmn_valid"] is True
    assert data["validation"]["dmn_valid"] is True
    assert data["validation"]["connectivity_ok"] is True

    res = data["resources"]
    assert "Family physician" in res["actors"]
    assert res["counts"]["evidence"] == 3
    # The Decision missing gateway_kind was normalized to XOR (exclusive gateway).
    decision = next(e for e in res["elements"] if e["type"] == "Decision")
    assert decision["gateway_kind"] == "XOR"
    # DMN table generated with FEEL rules.
    assert data["dmn_xml"] and res["decisions"][0]["inputs"] == ["IPSS improvement"]
    # Terminology grounded IPSS.
    assert any(m["surface_form"] == "IPSS" for a in res["terminology"] for m in a["mappings"])


def test_generate_rejects_empty_text(monkeypatch):
    client = TestClient(api.app)
    assert client.post("/api/generate", json={"text": ""}).status_code == 422
