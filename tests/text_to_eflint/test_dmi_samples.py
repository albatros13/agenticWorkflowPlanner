"""DMI Afsprakenstelsel samples: vendored reference integrity, the structural comparison of generated
eFLINT with the reference, the QA runner and the demo endpoints. Offline (scripted LLM).

The live generation-quality run is in ``test_dmi_quality_live.py``; results: docs/test_to_eflint/QA_EVAL.md.
"""
from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from agents.text_to_bpmn.pipeline.llm.mock import ScriptedLLMProvider
from agents.text_to_eflint.evaluation import qa
from agents.text_to_eflint.evaluation.compare import article_pattern, compare, similarity
from agents.text_to_eflint.evaluation.reference_scan import scan, scan_files
from agents.text_to_eflint.evaluation.samples import articles, dmi_path, get_sample, policy_path, samples
from agents.text_to_eflint.generation import EflintSpec
from agents.text_to_eflint.generation.prompts import TASK_DRAFT
from agents.text_to_eflint.generation.syntax import EflintSyntaxError, parse_program

SAMPLE_IDS = [s.id for s in samples()]

# A plausible English generation for the Commons sample (valid in the generator's eFLINT subset).
COMMONS_SPEC = {
    "title": "DMI Commons transaction",
    "facts": [
        {"name": "participant", "identified_by": ["String"], "source": "Afsprakenstelsel art. 2.1"},
        {"name": "product", "identified_by": ["String"], "source": "domain model"},
        {"name": "participant-admitted", "identified_by": ["participant"], "source": "Afsprakenstelsel art. 2.1",
         "source_quote": "Toetreding tot het DMI-ecosysteem vindt plaats door"},
        {"name": "offer", "identified_by": ["participant", "product"], "source": "Afsprakenstelsel art. 2.3c"},
        {"name": "offered-as-commons", "identified_by": ["participant"], "source": "Afsprakenstelsel art. 7.2"},
        {"name": "commercially-available", "identified_by": ["product"], "source": "Afsprakenstelsel art. 7.3"},
        {"name": "non-commercial-use-declared", "identified_by": ["participant", "product"],
         "source": "Afsprakenstelsel art. 7.13"},
        {"name": "agreement", "identified_by": ["participant", "participant", "product"], "source": "domain model"},
    ],
    "duties": [],
    "acts": [
        {"name": "publish-offer", "actor": "participant", "related_to": ["product"],
         "holds_when": "participant-admitted(participant)", "creates": ["offer(participant, product)"],
         "source": "Afsprakenstelsel art. 2.3c"},
        {"name": "sign-agreement", "actor": "participant", "related_to": ["product"],
         "holds_when": "participant-admitted(participant) && offered-as-commons(participant)",
         "source": "Afsprakenstelsel art. 7.2"},
    ],
    "instances": [{"term": 'participant("Amy")'}, {"term": 'participant("Bob")'}, {"term": 'product("Appel")'}],
    "initial_state": [{"term": 'participant-admitted("Amy")'}],
    "scenarios": [{"name": "Amy publishes", "steps": [
        {"kind": "perform", "term": 'publish-offer("Amy", "Appel")'},
        {"kind": "query", "term": 'offer("Amy", "Appel")', "query": "holds", "expect": True},
        {"kind": "perform", "term": 'publish-offer("Bob", "Appel")', "expect": False},
    ]}],
}


def base():
    return qa.base_sources()


def reference_named_spec(sample) -> EflintSpec:
    """A spec that declares exactly the reference's names (and its act preconditions)."""
    decls = scan_files(sample.reference_files()) + scan_files(base())
    hooks: dict[str, set[str]] = {}
    for d in decls:
        if d.kind in ("Act", "Event"):
            hooks.setdefault(d.name, set()).update(r for c in d.conditions for r in c.refs if not r.startswith("["))
    facts, acts, duties = [], [], []
    cite = " ".join(f"art. {a}" for a in sample.articles)
    for d in decls:
        if d.extend or d.kind == "Placeholder":
            continue
        if d.kind == "Duty":
            duties.append({"name": d.name, "holder": "x", "claimant": "y", "source": cite})
        elif d.kind in ("Act", "Event"):
            acts.append({"name": d.name, "actor": "x", "source": cite,
                         "holds_when": " && ".join(f"{r}()" for r in sorted(hooks.get(d.name, ()))) or None})
        else:
            facts.append({"name": d.name, "source": cite})
    return EflintSpec.model_validate({"facts": facts, "acts": acts, "duties": duties})


# --- vendored reference -----------------------------------------------------------------------------


def test_every_require_resolves_inside_the_vendored_policy():
    root = dmi_path("policy")
    for path in root.rglob("*.eflint"):
        for req in re.findall(r'^#require\s+"([^"]+)"', path.read_text(encoding="utf-8"), re.M):
            assert (path.parent / req).resolve().is_file(), f"{path.relative_to(root)} requires missing {req}"


def test_licence_and_provenance_are_vendored():
    assert "Apache License" in dmi_path("LICENSE").read_text(encoding="utf-8")
    readme = dmi_path("README.md").read_text(encoding="utf-8")
    assert "f4c8ea358667ed8f495e5ba27ccc2baa19a1e15b" in readme


def test_afsprakenstelsel_article_texts_are_verbatim_from_the_published_pdf():
    arts = articles()
    dmi = [a for a in arts.values() if not a.id.startswith("AVG")]
    assert len(dmi) == 13 and all(a.verified_in_pdf for a in dmi)
    for a in arts.values():
        assert a.text.strip() and policy_path(a.module).is_file()
    assert arts["7.13"].text.startswith("7.13 Inzage en gebruik van producten, diensten en data als Commons")


@pytest.mark.parametrize("sample_id", SAMPLE_IDS)
def test_sample_articles_are_the_ones_its_example_scenario_cites(sample_id):
    s = get_sample(sample_id)
    cited = set()
    for m in re.finditer(r"Article ([0-9.]+[a-z]?)(?:\s*&\s*([0-9.]+[a-z]?))?", s.example_source()):
        cited.update(x for x in m.groups() if x)
    assert cited == {a for a in s.articles if not a.startswith("AVG")}
    for module in s.modules:
        assert policy_path(module).is_file()
    assert all(a in s.text for a in ("Afsprakenstelsel", s.articles[0]))
    assert "No violation" in s.example_source()  # every upstream example is a compliant path


def test_the_reference_uses_eflint_beyond_the_generators_subset():
    """Documented limitation (QA_EVAL.md): the generator's parser cannot load the reference, so the
    comparison is structural. If this starts passing, scenario-level comparison becomes possible."""
    source = policy_path("artikel7/artikel7.2.eflint").read_text(encoding="utf-8")
    with pytest.raises(EflintSyntaxError):
        parse_program(source.replace('#require "../artikel1/artikel1.eflint".', ""), source="artikel7.2")


def test_scanner_recovers_declarations_hooks_and_duties():
    d23c = scan(policy_path("artikel2/artikel2.3c.eflint").read_text(encoding="utf-8"))
    assert [(d.extend, d.kind, d.name) for d in d23c] == [
        (True, "Act", "maak-aanbod"), (True, "Act", "reageer"), (True, "Act", "onderteken"), (True, "Act", "neem-af")]
    assert all(d.conditions[0].refs[0] == "deelnemer-lid-op" for d in d23c)

    d713 = {d.name: d for d in scan(policy_path("artikel7/artikel7.13.eflint").read_text(encoding="utf-8"))}
    duty = d713["commons-mag-niet-commercieel-afgenomen"]
    assert duty.kind == "Duty" and duty.params == ["afnemer", "[Ecoraad]", "overeenkomst"]
    assert [c.keyword for c in duty.conditions] == ["Holds when", "Violated when"]

    model = {d.name: d for d in scan(policy_path("artikel1/model.eflint").read_text(encoding="utf-8")) if not d.extend}
    assert {"maak-aanbod", "reageer", "onderteken", "neem-af"} <= {n for n, d in model.items() if d.kind == "Act"}


# --- comparison ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("sample_id", SAMPLE_IDS)
def test_reference_compared_with_itself_scores_full_marks(sample_id):
    s = get_sample(sample_id)
    summary = compare(reference_named_spec(s), s.reference_files(), base=base(), articles=s.articles).summary()
    for metric in ("concept_recall", "hook_recall", "domain_recall", "precision", "article_coverage"):
        assert summary[metric] == 1.0, (metric, summary)
    assert summary["duty_recall"] in (1.0, None)


def test_empty_spec_scores_zero():
    s = get_sample("dmi-commons-transaction")
    summary = compare(EflintSpec(), s.reference_files(), base=base(), articles=s.articles).summary()
    assert summary["concept_recall"] == summary["hook_recall"] == summary["article_coverage"] == 0.0
    assert summary["precision"] is None and summary["articles_missing"] == s.articles


def test_english_generation_is_matched_through_the_glossary():
    s = get_sample("dmi-commons-transaction")
    c = compare(EflintSpec.model_validate(COMMONS_SPEC), s.reference_files(), base=base(), articles=s.articles)
    summary = c.summary()
    matched = {m.reference: m.generated for m in c.domain_matches if m.generated}
    assert matched["aanbod"] == "offer" and matched["overeenkomst"] == "agreement"
    assert matched["deelnemer-toegetreden"] == "participant-admitted"
    # sign-agreement's precondition covers the 7.2 hook on onderteken (commons offered)
    hooks = {m.reference: m.generated for m in c.hook_matches}
    assert hooks["onderteken <- deelnemer-ooit-iets-aangeboden-als-commons-voor, [Deelnemer], datum"] == "sign-agreement"
    assert summary["duty_recall"] == 0.0  # the 7.13 duty was modelled as a fact, not a duty
    assert summary["articles_missing"] == ["7.9"] and summary["article_coverage"] == pytest.approx(5 / 6, abs=1e-3)
    assert 0 < summary["concept_recall"] < 1 and summary["precision"] > 0.5


@pytest.mark.parametrize("a, b, expected", [
    ("deelnemer-toegetreden", "participant-admitted", True),
    ("onderteken", "sign-agreement", True),
    ("afnemer-genoeg-budget", "consumer-has-sufficient-budget", True),
    ("aanbod-als-commons", "offer-as-commons", True),
    ("onderteken", "publish-offer", False),
    ("commons-budget", "pdx-costs-paid", False),
])
def test_name_similarity(a, b, expected):
    assert (similarity(a, b) >= 0.5) is expected


@pytest.mark.parametrize("article, text, hit", [
    ("7.13", "Afsprakenstelsel art. 7.13", True), ("7.1", "art. 7.13", False), ("7.1", "artikel 7.1 lid 2", True),
    ("2.3c", "Art. 2.3(c)", True), ("2.3c", "art. 2.3 c)", True), ("2.3c", "art. 2.3", False),
    ("AVG 6(1)", "AVG art. 6(1)(d)", True), ("AVG 6(1)", "GDPR Article 6.1", True), ("AVG 6(1)", "GDPR art. 6(4)", False),
    ("AVG 4", "AVG artikel 4", True),
])
def test_article_citations(article, text, hit):
    assert bool(article_pattern(article).search(text)) is hit


# --- QA runner (scripted LLM) ----------------------------------------------------------------------------


def test_qa_run_generates_compares_and_writes_a_report(tmp_path):
    provider = ScriptedLLMProvider({TASK_DRAFT: COMMONS_SPEC})
    report = qa.run(provider, sample_ids=["dmi-commons-transaction"], out=tmp_path, max_repair_rounds=0)
    assert [r["condition"] for r in report["runs"]] == list(qa.CONDITIONS)
    text_run, vocab_run = report["runs"]
    assert text_run["status"] == "succeeded" and text_run["checks"]["errors"] == 0
    assert text_run["checks"]["scenarios_passed"] == text_run["checks"]["scenarios_total"] == 1
    assert text_run["hook_recall"] > 0 and text_run["domain_recall"] > 0

    draft_text, draft_vocab = (c.prompt for c in provider.calls)
    s = get_sample("dmi-commons-transaction")
    assert articles()["7.13"].text[:60] in draft_text and s.domain_context[:60] in draft_text
    assert "maak-aanbod" not in draft_text and "maak-aanbod" in draft_vocab  # only the vocabulary condition

    assert json.loads((tmp_path / "report.json").read_text())["reference"]["commit"].startswith("f4c8ea35")
    run_dir = tmp_path / "dmi-commons-transaction__text"
    assert (run_dir / "spec.eflint").is_file() and json.loads((run_dir / "result.json").read_text())["sample"]
    table = qa.summary_table(report)
    assert table.count("\n") == 3 and "dmi-commons-transaction" in table


def test_qa_run_records_a_failed_generation_and_continues(tmp_path):
    report = qa.run(ScriptedLLMProvider({}), sample_ids=["dmi-normal-transaction"], conditions=("text",))
    [row] = report["runs"]
    assert row["status"] == "error" and "no response registered" in row["error"]
    assert "| error |" in qa.summary_table(report)


# --- demo --------------------------------------------------------------------------------------------------


def test_demo_lists_loads_and_compares_samples():
    import agents.text_to_eflint.demo.main as demo

    client = TestClient(demo.app)
    listing = client.get("/api/samples").json()
    assert listing["count"] == 4 and {s["id"] for s in listing["samples"]} == set(SAMPLE_IDS)

    detail = client.get("/api/samples/dmi-gdpr-transaction").json()
    assert detail["language"] == "nl" and "De verwerking is alleen rechtmatig" in detail["text"]
    assert "artikel3/gdpr.eflint" in detail["reference"]["files"]
    assert detail["reference"]["example"]["source"].startswith("// PASS GDPR TRANSACTION")
    assert detail["provenance"]["license"] == "Apache-2.0"
    assert client.get("/api/samples/nope").status_code == 404

    cmp = client.post("/api/samples/dmi-commons-transaction/compare", json={"spec": COMMONS_SPEC}).json()
    assert cmp["domain_recall"] > 0 and cmp["articles_missing"] == ["7.9"]
    assert client.post("/api/samples/dmi-commons-transaction/compare", json={"spec": {"facts": 3}}).status_code == 422


def test_demo_page_offers_the_samples():
    import agents.text_to_eflint.demo.main as demo

    page = TestClient(demo.app).get("/").text
    assert 'id="sampleSel"' in page and "/api/samples" in page and "compareWithReference" in page
