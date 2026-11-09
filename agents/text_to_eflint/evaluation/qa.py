"""Generation-quality evaluation: generate eFLINT for each sample and compare it with the reference.

    python -m agents.text_to_eflint.evaluation.qa --out storage/qa/dmi        # all samples, both conditions
    python -m agents.text_to_eflint.evaluation.qa --samples dmi-commons-transaction --conditions text

Two input conditions per sample:

* ``text`` — the regulatory text plus the scenario's application context (what a user supplies);
* ``text+vocabulary`` — additionally the reference's *transaction model* (the base vocabulary of
  ``artikel1/model.eflint``), which the Afsprakenstelsel text itself does not define. Comparing the
  two shows how much of the gap is vocabulary rather than norm extraction.

Each run writes its rendered ``.eflint`` files and ``result.json`` under ``<out>/<sample>__<condition>/``
and the whole run a ``report.json`` (see :func:`run`). Needs a configured LLM provider.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from ..generation import EflintGenerator
from .compare import compare
from .samples import DMI_COMMIT, DMI_REPOSITORY, Sample, policy_path, samples

log = logging.getLogger(__name__)

CONDITIONS = ("text", "text+vocabulary")
BASE_MODULES = [f"artikel1/{n}.eflint" for n in ("model", "entiteiten", "commons", "pdd", "kosten", "tijd")]

# The reference's transaction model (artikel1/model.eflint), described in words: design decisions of
# the modeller that are not in the regulatory text.
VOCABULARY_HINT = (
    "Use the transaction model of the DMI Afsprakenstelsel formalisation and its Dutch names: "
    "participants are 'deelnemer'; an offer is 'aanbod' (made by the 'aanbieder' for a product, service "
    "or data, a 'PDD', in a given year); a response to an offer is 'reactie' (by the 'afnemer'); once both "
    "parties have signed a response it is an agreement, 'overeenkomst'; delivery under an agreement is a "
    "'transactie'. The four acts are 'maak-aanbod' (a participant publishes an offer in the Products and "
    "Services Catalogue), 'reageer' (a participant responds to an offer), 'onderteken' (a party signs a "
    "response) and 'neem-af' (the afnemer takes the PDD from the provider under an agreement). The "
    "articles add preconditions to these acts and duties on the parties; the Ecoraad is the claimant of "
    "duties towards the ecosystem."
)


def base_sources() -> dict[str, str]:
    return {m: policy_path(m).read_text(encoding="utf-8") for m in BASE_MODULES}


def domain_context(sample: Sample, condition: str) -> str:
    if condition == "text":
        return sample.domain_context
    if condition == "text+vocabulary":
        return f"{sample.domain_context}\n\n{VOCABULARY_HINT}"
    raise ValueError(f"unknown condition {condition!r} (expected one of {CONDITIONS})")


def evaluate(spec, report, sample: Sample) -> dict:
    """Structural comparison with the reference + the generator's own check results."""
    comparison = compare(spec, sample.reference_files(), base=base_sources(), articles=sample.articles)
    summary = report.summary()
    scen_total = summary.get("scenarios_total", 0)
    return {
        **comparison.summary(),
        "checks": {
            "errors": summary.get("errors", 0),
            "warnings": summary.get("warnings", 0),
            "scenarios_passed": summary.get("scenarios_passed", 0),
            "scenarios_total": scen_total,
        },
        "details": comparison.details(),
    }


def run_one(generator: EflintGenerator, sample: Sample, condition: str, out: Path | None = None) -> dict:
    started = time.monotonic()
    row = {"sample": sample.id, "condition": condition, "articles": sample.articles}
    try:
        result = generator.generate(sample.text, title=sample.title, domain_context=domain_context(sample, condition))
    except Exception as exc:  # noqa: BLE001 - one failed sample must not end the evaluation
        log.exception("generation failed for %s/%s", sample.id, condition)
        row.update(status="error", error=f"{type(exc).__name__}: {exc}",
                   elapsed_s=round(time.monotonic() - started, 1))
        return row
    row.update(status=result.status, rounds=result.rounds, history=result.history, notes=result.notes,
               elapsed_s=round(time.monotonic() - started, 1), **evaluate(result.spec, result.report, sample))
    if out is not None:
        run_dir = out / f"{sample.id}__{condition.replace('+', '-')}"
        run_dir.mkdir(parents=True, exist_ok=True)
        for name, content in result.files.items():
            (run_dir / name).write_text(content, encoding="utf-8")
        (run_dir / "spec.json").write_text(result.spec.model_dump_json(indent=2), encoding="utf-8")
        (run_dir / "result.json").write_text(json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8")
    return row


def run(provider, *, sample_ids: list[str] | None = None, conditions: tuple[str, ...] = CONDITIONS,
        out: Path | None = None, max_repair_rounds: int = 2) -> dict:
    chosen = [s for s in samples() if not sample_ids or s.id in sample_ids]
    rows = []
    for sample in chosen:
        generator = EflintGenerator(provider, max_repair_rounds=max_repair_rounds, language=sample.language)
        for condition in conditions:
            log.info("generating %s (%s)", sample.id, condition)
            rows.append(run_one(generator, sample, condition, out))
    report = {
        "reference": {"repository": DMI_REPOSITORY, "commit": DMI_COMMIT},
        "provider": getattr(provider, "name", ""), "model": getattr(provider, "model", ""),
        "max_repair_rounds": max_repair_rounds,
        "runs": rows,
    }
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def summary_table(report: dict) -> str:
    """The headline metrics of a report as a Markdown table."""
    head = ("| sample | condition | status | rounds | errors | scenarios | concept | duty | hook | domain | "
            "precision | articles |")
    lines = [head, "|" + "---|" * 12]

    def pct(v):
        return "–" if v is None else f"{v:.0%}"

    for r in report["runs"]:
        if r["status"] == "error":
            lines.append(f"| {r['sample']} | {r['condition']} | error | | | | | | | | | |")
            continue
        c = r["checks"]
        lines.append(
            f"| {r['sample']} | {r['condition']} | {r['status']} | {r['rounds']} | {c['errors']} | "
            f"{c['scenarios_passed']}/{c['scenarios_total']} | {pct(r['concept_recall'])} | {pct(r['duty_recall'])} | "
            f"{pct(r['hook_recall'])} | {pct(r['domain_recall'])} | {pct(r['precision'])} | "
            f"{pct(r['article_coverage'])} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - needs an LLM
    from agents.text_to_bpmn.shaping import default_provider  # noqa: PLC0415

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--samples", nargs="*")
    ap.add_argument("--conditions", nargs="*", default=list(CONDITIONS), choices=CONDITIONS)
    ap.add_argument("--max-repair-rounds", type=int, default=2)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    report = run(default_provider(), sample_ids=args.samples, conditions=tuple(args.conditions), out=args.out,
                 max_repair_rounds=args.max_repair_rounds)
    print(summary_table(report))


if __name__ == "__main__":  # pragma: no cover
    main()
