"""The document library uploads, finds, and loads documents idempotently."""
import pytest

from agents.regulatory_docs.library import DocumentLibrary

MD = """# Data Protection Act

## Article 5
The controller shall process personal data lawfully.

## Article 6
Processing is lawful only if the data subject has given consent.
"""


def test_add_from_text_stores_and_parses(tmp_path):
    lib = DocumentLibrary(tmp_path)
    rec = lib.add_from_text(MD, title="DPA")
    assert rec.title == "DPA"
    assert rec.kind == "text"
    assert rec.n_blocks > 0 and rec.n_chars == len(MD)
    assert "Data Protection Act" in rec.preview
    # Files written under <root>/<doc_id>/
    assert (tmp_path / rec.doc_id / "document.json").is_file()
    assert (tmp_path / rec.doc_id / "meta.json").is_file()
    # Parsed structure is retrievable with section locations preserved.
    doc = lib.load_document(rec.doc_id)
    assert any(b.location.section for b in doc.blocks)


def test_upload_is_idempotent_by_content(tmp_path):
    lib = DocumentLibrary(tmp_path)
    r1 = lib.add_from_text(MD, title="DPA")
    r2 = lib.add_from_text(MD, title="DPA again")
    assert r1.doc_id == r2.doc_id
    assert r2.title == "DPA"  # existing record returned unchanged
    assert len(lib.find()) == 1


def test_empty_text_rejected(tmp_path):
    lib = DocumentLibrary(tmp_path)
    with pytest.raises(ValueError):
        lib.add_from_text("   ")


def test_find_filters_by_query(tmp_path):
    lib = DocumentLibrary(tmp_path)
    lib.add_from_text(MD, title="Data Protection Act")
    lib.add_from_text("# Tax Code\nTaxpayers must file annually.", title="Tax Code")
    assert len(lib.find()) == 2
    hits = lib.find("tax")
    assert len(hits) == 1 and hits[0].title == "Tax Code"
    assert lib.find("nonexistent-term") == []


def test_add_from_path_markdown(tmp_path):
    src = tmp_path / "reg.md"
    src.write_text(MD, encoding="utf-8")
    lib = DocumentLibrary(tmp_path / "lib")
    rec = lib.add_from_path(src)
    assert rec.kind == "markdown"
    assert rec.source_document == "reg.md"
    assert lib.load_document(rec.doc_id).id == rec.doc_id


def test_record_vectorization_updates_meta(tmp_path):
    lib = DocumentLibrary(tmp_path)
    rec = lib.add_from_text(MD, title="DPA")
    updated = lib.record_vectorization(rec.doc_id, {"collection": "c", "cluster": "memory", "dimension": 8, "points": 3})
    assert updated.vectorized is True
    assert updated.vectorizations[0]["collection"] == "c"
    # Re-recording the same collection replaces rather than duplicates.
    again = lib.record_vectorization(rec.doc_id, {"collection": "c", "cluster": "memory", "dimension": 8, "points": 4})
    assert len([v for v in again.vectorizations if v["collection"] == "c"]) == 1
    assert again.vectorizations[-1]["points"] == 4


def test_get_missing_raises(tmp_path):
    lib = DocumentLibrary(tmp_path)
    with pytest.raises(KeyError):
        lib.get("deadbeef")
