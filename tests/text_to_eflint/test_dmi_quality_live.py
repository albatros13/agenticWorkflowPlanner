"""Live generation-quality test: real LLM, every DMI sample, both input conditions.

Opt-in (costs LLM calls, ~10-20 min)::

    EFLINT_QA_LIVE=1 EFLINT_QA_OUT=storage/qa/dmi .venv/bin/python -m pytest tests/text_to_eflint/test_dmi_quality_live.py -s

The report (``report.json``, per-run ``.eflint`` files) goes to ``EFLINT_QA_OUT`` (default: a pytest
temp dir). Only floors are asserted — the measured quality is documented in
docs/test_to_eflint/QA_EVAL.md, not encoded as pass/fail thresholds.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agents.text_to_eflint.evaluation import qa

pytestmark = pytest.mark.skipif(os.getenv("EFLINT_QA_LIVE") != "1",
                                reason="live LLM evaluation; set EFLINT_QA_LIVE=1 to run")


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    from agents.text_to_bpmn.pipeline.llm.base import LLMError
    from agents.text_to_bpmn.shaping import default_provider

    try:
        provider = default_provider()
    except LLMError as exc:
        pytest.skip(f"no LLM provider: {exc}")
    out = Path(os.getenv("EFLINT_QA_OUT") or tmp_path_factory.mktemp("dmi-qa"))
    result = qa.run(provider, out=out)
    print("\n" + qa.summary_table(result) + f"\n\nreport: {out / 'report.json'}")
    return result


def test_every_sample_generates_in_both_conditions(report):
    assert len(report["runs"]) == 4 * len(qa.CONDITIONS)
    failed = [(r["sample"], r["condition"], r.get("error")) for r in report["runs"] if r["status"] == "error"]
    assert not failed


def test_generated_specs_have_normative_content_and_cite_the_articles(report):
    for r in report["runs"]:
        if r["status"] == "error":
            continue
        assert r["counts"]["generated"]["act"] >= 1, r["sample"]
        assert r["concept_recall"] > 0 and r["article_coverage"] > 0, (r["sample"], r["condition"])
