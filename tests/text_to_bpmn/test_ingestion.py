from pathlib import Path

from agents.text_to_bpmn.pipeline.ingestion.base import BlockType
from agents.text_to_bpmn.pipeline.ingestion.chunker import SemanticChunker
from agents.text_to_bpmn.pipeline.ingestion.markdown_loader import MarkdownLoader

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def test_markdown_preserves_structure_and_locations():
    doc = MarkdownLoader().load(FIXTURES / "cases" / "02_xor_decision.md")
    headings = [b for b in doc.blocks if b.type == BlockType.HEADING]
    list_items = [b for b in doc.blocks if b.type == BlockType.LIST_ITEM]

    assert any(h.text == "Follow-up" for h in headings)
    assert len(list_items) == 2  # the two IPSS branches
    # Section path is tracked and line numbers are populated.
    assert all(b.location.section for b in list_items)
    assert all(b.location.line_start and b.location.line_start >= 1 for b in doc.blocks)


def test_table_block_kept_whole():
    md = "# T\n\n| Prostate size | Procedure |\n| --- | --- |\n| < 30 g | TURP |\n| > 60 g | Adenomectomy |\n"
    doc = MarkdownLoader().load_text(md, source_document="t.md", doc_id="t")
    tables = [b for b in doc.blocks if b.type == BlockType.TABLE]
    assert len(tables) == 1
    assert "TURP" in tables[0].text and "Adenomectomy" in tables[0].text


def test_chunks_carry_section_and_block_ids():
    doc = MarkdownLoader().load(FIXTURES / "smoke_bph.md")
    chunks = SemanticChunker().chunk(doc)
    assert chunks
    assert all(c.block_ids for c in chunks)
    assert any("Follow-up" in c.section for c in chunks)
