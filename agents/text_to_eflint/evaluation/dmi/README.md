# DMI Afsprakenstelsel — reference formalisation (vendored)

A pinned copy of the eFLINT formalisation of the *Afsprakenstelsel* (participation agreement) of the
DMI ecosystem, from
[eflint/formalisations/dmi-afsprakenstelsel](https://gitlab.com/eflint/formalisations/dmi-afsprakenstelsel),
branch `main`, commit `f4c8ea358667ed8f495e5ba27ccc2baa19a1e15b` (2026-02-10). It is Apache-2.0 licensed;
see [`LICENSE`](LICENSE). The authors named in the module headers are Tim Müller, Marten Steketee and
Thomas van Binsbergen.

The formalised legal text is *Bijlage 7: Afsprakenstelsel*, version 1.1 (1 June 2023), in Dutch. The
upstream repository includes the PDF.

| path | content |
|---|---|
| `policy/afsprakenstelsel.eflint` | the whole policy, which `#require`s every module below |
| `policy/artikel1/` | definitions and the transaction model (offer → response → sign → take), time and costs |
| `policy/artikel2/`, `artikel3/`, `artikel7/` | one module per enforced article: 2.1, 2.3c, 3.1 (+ `gdpr.eflint`: AVG 4, 5(1)(b), 6(1), 6(4), 7(1), 12(1)), 7.1, 7.2, 7.3, 7.6, 7.8, 7.9, 7.12, 7.13, 7.15, 7.16 |
| `policy/rating/hook.eflint`, `rating-model.eflint` | the rating hook that `afsprakenstelsel.eflint` requires |
| `policy/examples/afsprakenstelsel/pass-*-transaction-en.eflint` | the four upstream example scenarios: normal, Commons, data and GDPR transactions, each expected to pass with no violation |
| `articles.json` | the article texts (see below) |
| `samples.json` | one regulatory-text sample per example scenario (see below) |

## `articles.json`

Each article's text is the quote in the header comment of the module that formalises it, so it is
exactly the text the modeller formalised. All 13 Afsprakenstelsel quotes were checked verbatim
against the text extracted from the published PDF (`verified_in_pdf: true`). Where the modeller
formalised only one clause, `[...]` marks the omitted parts (art. 2.3c).

The AVG (GDPR) texts are the `>` quotes of the upstream `artikel3/docs/gdpr/*.md` notes, with the
markdown links removed. The notes' interpretation and formalisation sections are not included,
because they would hand the generator the modelling decisions.

## `samples.json`

There is one sample per example scenario. A sample has:

- `articles`: the articles the scenario cites in its comments;
- `modules`: the modules that formalise those articles (the comparison reference);
- `domain_context`: a paraphrase of the scenario's own English comments (participants, product, steps).

The demo ("Load sample") and the generation-quality evaluation
(`agents/text_to_eflint/evaluation/qa.py`, `docs/test_to_eflint/QA_EVAL.md`) both use them.

## Changes from upstream

- Only the files listed above are vendored. Not vendored: the PDF, the GDPR notes, `tests/`,
  `error-handling/`, the rating examples and the rest of `rating/`.
- `policy/` mirrors the upstream layout, so the `#require` paths resolve unchanged and the
  require graph of `afsprakenstelsel.eflint` is complete. `tests/text_to_eflint/test_dmi_samples.py`
  checks this.
