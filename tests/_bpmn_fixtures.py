"""Shared scripted-LLM fixture: a small follow-up process used by agent/coordinator tests.

Mirrors the offline setup in ``tests/test_api.py`` so the text->BPMN agent and the coordinator
can be exercised end-to-end without any API key.
"""
from agents.text_to_bpmn.pipeline.ingestion.chunker import SemanticChunker
from agents.text_to_bpmn.pipeline.ingestion.markdown_loader import MarkdownLoader
from agents.text_to_bpmn.pipeline.llm.mock import ScriptedLLMProvider

TEXT = (
    "# Follow-up\n\n"
    "The family physician assesses the IPSS. If improvement is at least 25%, "
    "continue treatment. If improvement is less than 25%, refer back to the urologist.\n"
)


def scripted_provider() -> ScriptedLLMProvider:
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
