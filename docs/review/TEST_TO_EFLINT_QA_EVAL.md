# text_to_eflint — generation quality on the DMI Afsprakenstelsel

*Date: 2026-11-09. Scope: the demo samples and the functional/quality tests built from
[`examples/afsprakenstelsel/`](https://gitlab.com/eflint/formalisations/dmi-afsprakenstelsel/-/tree/main/examples/afsprakenstelsel)
of the DMI Afsprakenstelsel formalisation (commit `f4c8ea35`, Apache-2.0).*

## 1. What the source contains, and what it does not

The four files in `examples/afsprakenstelsel/` (`pass-{normal,commons,data,gdpr}-transaction-en.eflint`) are
**scenarios, not regulatory text**. Each one is a transaction between two participants, Amy and Bob,
that is expected to pass the whole policy ("No violation!! Policy passed!!"). Each one
`#require`s `../../afsprakenstelsel.eflint`, the policy itself. Building samples and reference
comparisons from them therefore needed three more pieces of the upstream repository:

| needed | where it was found |
|---|---|
| the regulatory text | Each article module (`artikel*/artikel*.eflint`) quotes the article it formalises in its header comment. I checked all 13 quotes verbatim against the published PDF (`Bijlage beleidsregels deelvoertuigen DMI Afsprakenstelsel.pdf`, v1.1 of 1 June 2023). The GDPR (AVG) texts come from the `>` quotes in `artikel3/docs/gdpr/*.md`. |
| which articles a scenario exercises | The scenario comments ("Article 2.1 & 2.3c", "Article 7.2", …) |
| the reference formalisation of those articles | The cited modules, plus the base model `artikel1/` they build on |

These are vendored in `agents/text_to_eflint/evaluation/dmi/`; its `README.md` documents provenance and changes.

### Missing information

1. **No English regulatory text.** The examples are "-en" only in their comments. The
   Afsprakenstelsel and the AVG quotes exist only in Dutch, and no official English version is
   in the repository. The samples are therefore Dutch (`language: nl`). They were **not**
   machine-translated: a translated legal text would no longer be the text the reference
   formalises. The generator's prompt and few-shot reference are English, so every run is
   cross-lingual: Dutch text in, and identifiers in either language out.
2. **The transaction model is not in the text.** Every article module hooks into four acts
   (`maak-aanbod`, `reageer`, `onderteken`, `neem-af`) and the facts `aanbod` / `reactie` /
   `overeenkomst` / `transactie`, all defined in `artikel1/model.eflint`. They are the modeller's
   representation of how the Products and Services Catalogue works. No article states them, and
   the definitions in Article 1 do not define them either. A generator that sees only the
   articles cannot reproduce this vocabulary. The evaluation therefore has a second input
   condition, `text+vocabulary`, which also describes the transaction model in words
   (`qa.VOCABULARY_HINT`). This isolates how much of the gap is vocabulary rather than norm
   extraction.
3. **The modeller's interpretation decisions are only in comments.** Several articles are only
   partly formalised, or re-interpreted:
   - 7.2: only "has offered something as Commons at least once" plus a budget check;
   - 7.3: commercial availability rather than the Eurowaarde;
   - 7.12: clause (ii) is left to art. 3.1;
   - 2.1: "the registration tells us" instead of checking the form and payment steps.

   These decisions are written in the Dutch prose of each module header, after the quote. The
   generator is not given them, so differences that come from these choices count against it.
4. **The reference cannot be executed here.** The DMI policy uses full eFLINT: `Extend`,
   `Derived from`, `Placeholder`, `Bool`, `Invariant`, `Count`/`Sum`, `[Bracketed]` type names
   and primed variables. The generator's parser and simulator implement only the subset the
   generator emits; `test_the_reference_uses_eflint_beyond_the_generators_subset` pins this down.
   No eFLINT reasoner is part of this repository, so neither the reference scenarios nor a
   generated spec can be run against each other's scenarios. **Scenario-level equivalence was
   therefore not tested.** The comparison is structural (§2).
5. **No shared vocabulary between a generated spec and the reference.** Even with a reasoner, the
   upstream scenarios call `maak-aanbod("Amy", "a1", "Appel", datum(2025, 10, 1))`. A generated
   spec has its own names, arities and no `datum` type. Running the upstream examples against
   generated eFLINT would need a name and arity mapping, which does not exist.
6. **Upstream tests are not a labelled ground truth for generation.** `tests/afsprakenstelsel/*.ok|fail.eflint`
   exercise the same vocabulary, so they share the problem in point 5. They are not vendored.

## 2. Method

**Samples** (`evaluation/dmi/samples.json`): there is one sample per upstream example.

| sample | articles (cited by the example) | reference modules |
|---|---|---|
| `dmi-normal-transaction` | 2.1, 2.3c, 7.2 | 3 |
| `dmi-commons-transaction` | 2.1, 2.3c, 7.2, 7.3, 7.9, 7.13 | 6 |
| `dmi-data-transaction` | 2.1, 2.3c, 7.2, 7.12 | 4 |
| `dmi-gdpr-transaction` | 2.1, 2.3c, 3.1, 7.2, 7.12 + AVG 4, 5(1)(b), 6(1), 6(4), 7(1), 12(1) | 6 (incl. `gdpr.eflint`) |

The generator receives the Dutch article texts, with a citation line per article, and an
application context paraphrased from the example's English comments. This covers the participants,
the product or data, the transaction steps and "expected outcome: no violation".

**Comparison** (`evaluation/compare.py`): a full-dialect declaration scanner
(`evaluation/reference_scan.py`) extracts the reference's declarations and its norms on acts. The
comparator then measures:

| metric | meaning |
|---|---|
| **concept recall** | share of the reference's own declarations for the cited articles (facts, duties, invariants; type-population `Extend … Derived from (Foreach …)` plumbing excluded) that have a same-kind counterpart in the generated spec |
| **duty recall** | the same, for duties only |
| **hook recall** | share of the reference's act preconditions (`Extend Act x Conditioned by c`) for which the generated counterpart of `x` has a precondition that mentions a counterpart of something `c` refers to |
| **domain recall** | share of the base-model concepts (`artikel1`) the cited modules use (`aanbod`, `reactie`, `overeenkomst`, the four acts, `deelnemer-toegetreden`, `commons-budget`, …) that have a counterpart |
| **precision** | share of generated declarations that correspond to some reference or base declaration |
| **article coverage** | share of cited articles that some generated declaration cites in its `source` |
| **checks** | the generator's own result: errors, warnings, scenarios passed (its built-in simulator), repair rounds |

Names are matched by a Dice score of at least 0.5 over normalised tokens: lower-cased, stop words
removed, inflections tolerated through a shared prefix of at least 5 letters, and mapped through a
small explicit Dutch/English glossary of DMI and GDPR terms (`compare.GLOSSARY`). The glossary is
the only domain knowledge in the metric. A self-check confirms the comparator gives a spec that
uses the reference's own names 100 % on every metric (`test_reference_compared_with_itself_scores_full_marks`).

**Caveats of the metric.**
- It rewards declaring the same concepts, not the same semantics: two specs with equal names can
  still disagree on conditions.
- A correct model with different granularity is penalised. For example, a single
  `participant-admitted` fact scores against the reference's three facts for form, legal check and
  payment.
- The name matching can fail across languages for words the glossary lacks.

The numbers are indicators for comparing runs and conditions, not a correctness verdict.

**Tests**

- Offline, always run: `tests/text_to_eflint/test_dmi_samples.py`, 35 tests. They cover the
  vendored reference integrity (require graph closed, licence, quotes verbatim, each sample's
  articles equal to its example's citations), the scanner, the comparator (self-comparison,
  empty spec, an English generation via the glossary, name similarity, article-citation
  patterns), the QA runner with a scripted LLM, and the demo endpoints and page.
- Live, opt-in: `tests/text_to_eflint/test_dmi_quality_live.py` (`EFLINT_QA_LIVE=1`). It runs
  every sample in both conditions against the configured LLM and asserts only floors (no crash, at
  least one act, some concept recall and article coverage). The measured quality is recorded
  below, not encoded as thresholds.

## 3. Results

*(filled in from the live run below)*
