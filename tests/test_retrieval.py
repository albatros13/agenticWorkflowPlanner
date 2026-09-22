"""Guideline Step 2: for each fixture case, the relevant source passage can be
retrieved, and retrieval returns the ORIGINAL text plus its location."""
from pathlib import Path

import pytest

from src.ingestion.chunker import SemanticChunker
from src.ingestion.markdown_loader import MarkdownLoader
from src.retrieval.in_memory import InMemoryEvidenceIndex

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "cases"

CASES = [
    ("01_sequential.md", "physical examination", "physical examination"),
    ("02_xor_decision.md", "25% improvement IPSS referred urologist", "25%"),
    ("03_and_concurrent.md", "chest x-ray bone scan parallel", "parallel"),
    ("04_temporal.md", "radiotherapy two months after indication", "two months"),
    ("05_exception.md", "complication readmitted", "complication"),
    ("06_ambiguous.md", "surveillance considered life expectancy", "considered"),
]


@pytest.mark.parametrize("filename,query,expected", CASES)
def test_relevant_passage_retrieved(filename, query, expected):
    doc = MarkdownLoader().load(FIXTURES / filename)
    chunks = SemanticChunker().chunk(doc)
    index = InMemoryEvidenceIndex()
    index.index(chunks)

    results = index.search(query, k=3)
    assert results, f"no retrieval hits for {filename}"
    top = results[0]
    assert expected.lower() in top.text.lower()
    # Original location is preserved (traceability).
    assert top.location.line_start is not None
