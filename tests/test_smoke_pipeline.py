"""Guideline Step 1 smoke test: a minimal input passes through every implemented
pipeline stage (ingest -> evidence -> IR -> terminology) using a mocked LLM, and
the pipeline fails explicitly when a required upstream artifact is missing."""
from pathlib import Path

import pytest

from src.ir.enums import ElementType, GatewayKind, Uncertainty
from src.llm.mock import ScriptedLLMProvider
from src.pipeline.artifacts import ArtifactStore, MissingArtifactError
from src.pipeline.runner import Pipeline

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _scripted_provider(chunk_by_section):
    med = chunk_by_section("Medical treatment")
    fup = chunk_by_section("Follow-up")
    return ScriptedLLMProvider({
        "evidence_extraction": {
            "items": [
                {"id": "E1", "text": "Prescribe finasteride or an alpha-blocker.",
                 "source_chunk_id": med, "confidence": 0.95},
                {"id": "E2", "text": "If IPSS improves by at least 25%, continue treatment.",
                 "source_chunk_id": fup, "confidence": 0.9},
                {"id": "E3", "text": "If improvement is less than 25%, refer back to the urologist.",
                 "source_chunk_id": fup, "confidence": 0.9},
            ]
        },
        "logic_interpretation": {
            "name": "BPH medical treatment",
            "actors": ["Urologist", "Family physician"],
            "elements": [
                {"id": "S", "type": "Start", "name": "BPH confirmed"},
                {"id": "A1", "type": "Activity", "actor": "Urologist",
                 "name": "Prescribe medical treatment (finasteride or alpha-blocker)",
                 "evidence_refs": ["E1"]},
                {"id": "D1", "type": "Decision", "name": "IPSS improved >= 25%?",
                 "gateway_kind": "XOR", "evidence_refs": ["E2", "E3"],
                 "thresholds": [{"measure": "IPSS improvement", "operator": ">=",
                                 "value": 25, "unit": "%"}]},
                {"id": "A2", "type": "Activity", "actor": "Family physician",
                 "name": "Continue treatment", "evidence_refs": ["E2"]},
                {"id": "A3", "type": "Activity", "actor": "Family physician",
                 "name": "Refer back to urologist", "evidence_refs": ["E3"]},
                {"id": "END", "type": "End", "name": "Done"},
            ],
            "relations": [
                {"id": "r1", "type": "sequence", "source": "S", "target": "A1"},
                {"id": "r2", "type": "sequence", "source": "A1", "target": "D1"},
                {"id": "r3", "type": "conditional", "source": "D1", "target": "A2",
                 "condition": "IPSS improvement >= 25%"},
                {"id": "r4", "type": "conditional", "source": "D1", "target": "A3",
                 "condition": "IPSS improvement < 25%"},
                {"id": "r5", "type": "sequence", "source": "A2", "target": "END"},
                {"id": "r6", "type": "sequence", "source": "A3", "target": "END"},
            ],
            "uncertainties": [],
        },
    })


def test_full_pipeline_through_terminology(tmp_path):
    store = ArtifactStore(tmp_path / "run1")
    # Discover chunk ids deterministically to wire the scripted provider.
    from src.ingestion.chunker import SemanticChunker
    from src.ingestion.markdown_loader import MarkdownLoader

    doc = MarkdownLoader().load(FIXTURES / "smoke_bph.md")
    chunks = SemanticChunker().chunk(doc)

    def chunk_by_section(keyword):
        return next(c.id for c in chunks if keyword in c.section)

    provider = _scripted_provider(chunk_by_section)
    pipe = Pipeline(provider, store=store)

    process = pipe.run_through_terminology(FIXTURES / "smoke_bph.md", process_name="BPH")

    # IR is well-formed and preserves semantics.
    d1 = process.element("D1")
    assert d1.type is ElementType.DECISION and d1.gateway_kind is GatewayKind.XOR
    assert d1.thresholds[0].value == 25 and d1.thresholds[0].operator == ">="
    assert {r.condition for r in process.outgoing("D1")} == {
        "IPSS improvement >= 25%", "IPSS improvement < 25%"
    }

    # Every artifact was persisted (auditability).
    for name in ("document", "chunks", "evidence", "ir", "terminology"):
        assert store.has(name)

    # Terminology annotated finasteride + IPSS without altering names.
    annotations = store.load("terminology")
    mapped = {m["surface_form"] for a in annotations for m in a["mappings"]}
    assert {"finasteride", "IPSS"} <= mapped
    assert process.element("A1").name.startswith("Prescribe medical treatment")

    # Stage 5: deterministic BPMN + DMN compilation, schema-valid and connected.
    from src.validation.schema import check_connectivity, validate_bpmn, validate_dmn

    model = pipe.compile_bpmn()
    assert store.has("bpmn") and store.has("dmn")
    assert validate_bpmn(model.bpmn_xml).ok
    assert validate_dmn(model.dmn_xml).ok
    assert check_connectivity(model.bpmn_xml).ok


def test_pipeline_fails_explicitly_on_missing_artifact(tmp_path):
    store = ArtifactStore(tmp_path / "run2")
    provider = ScriptedLLMProvider({})  # never called
    pipe = Pipeline(provider, store=store)

    # No ingest() was run, so 'document'/'chunks' artifacts are absent.
    with pytest.raises(MissingArtifactError):
        pipe.extract_evidence()


def test_derived_element_without_evidence_is_allowed_when_traced(tmp_path):
    """A structural/derived element with no direct evidence must record provenance,
    exercising the Step 4 rule end-to-end through the interpreter."""
    store = ArtifactStore(tmp_path / "run3")
    from src.ingestion.chunker import SemanticChunker
    from src.ingestion.markdown_loader import MarkdownLoader

    doc = MarkdownLoader().load(FIXTURES / "smoke_bph.md")
    chunks = SemanticChunker().chunk(doc)
    med = next(c.id for c in chunks if "Medical treatment" in c.section)

    provider = ScriptedLLMProvider({
        "evidence_extraction": {
            "items": [{"id": "E1", "text": "Prescribe finasteride.", "source_chunk_id": med}]
        },
        "logic_interpretation": {
            "elements": [
                {"id": "S", "type": "Start", "name": "start"},
                {"id": "A1", "type": "Activity", "name": "Prescribe finasteride",
                 "evidence_refs": ["E1"]},
                {"id": "G1", "type": "Gateway", "name": "join", "gateway_kind": "AND",
                 "status": "DERIVED", "derived_from": ["A1"]},
            ],
            "relations": [
                {"id": "r1", "type": "sequence", "source": "S", "target": "A1"},
                {"id": "r2", "type": "sequence", "source": "A1", "target": "G1"},
            ],
        },
    })
    pipe = Pipeline(provider, store=store)
    pipe.ingest(FIXTURES / "smoke_bph.md")
    pipe.extract_evidence()
    process = pipe.interpret()
    assert process.element("G1").status is Uncertainty.DERIVED
