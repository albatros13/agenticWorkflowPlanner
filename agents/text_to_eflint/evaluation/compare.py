"""Compare a generated :class:`EflintSpec` with a reference formalisation, structurally.

The reference cannot be executed by the generator's simulator (see :mod:`.reference_scan`), and a
generated spec does not share the reference's vocabulary, so scenario-level equivalence cannot be
tested. What *can* be measured is whether the generated spec declares the same normative content:

* **concept recall** — the reference's own declarations for the cited articles (facts, acts,
  duties, invariants, excluding type-population plumbing) that have a counterpart of a compatible
  kind in the generated spec;
* **hook recall** — the reference's ``Extend Act x Conditioned by c`` / ``Holds when`` norms on an
  act: the generated counterpart of ``x`` must have a precondition that refers to a counterpart
  of something ``c`` refers to;
* **duty recall** — reference duties with a generated duty counterpart;
* **domain recall** — the base-model concepts (``artikel1``: offer, response, agreement, the four
  transaction acts, ...) the cited modules rely on;
* **precision** — generated declarations that correspond to anything in the reference;
* **article coverage** — cited articles that the generated declarations cite in ``source``.

Names are matched on normalised tokens: lower-cased, stop words dropped, inflection-tolerant
(shared prefix of at least 5 letters) and mapped through :data:`GLOSSARY`, a small explicit
Dutch/English glossary of DMI and GDPR terms, because the reference names are Dutch while the
generator may answer in either language. The glossary is the only domain knowledge in the metric.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..generation.spec import EflintSpec
from .reference_scan import Declaration, scan_files

MATCH_THRESHOLD = 0.5

# canonical concept -> surface tokens (Dutch and English, incl. inflections that a prefix won't catch)
GLOSSARY: dict[str, tuple[str, ...]] = {
    "participant": ("deelnemer", "deelnemers", "participant", "member", "lid", "leden", "deelname",
                    "participation", "membership"),
    "offer": ("aanbod", "aanbieden", "aangeboden", "aanbieding", "offer", "offered", "offering"),
    "provider": ("aanbieder", "provider", "supplier", "offeror", "seller", "producer"),
    "response": ("reactie", "reageer", "reageren", "respond", "response", "reply", "accept", "acceptance"),
    "agreement": ("overeenkomst", "agreement", "contract", "deal"),
    "sign": ("onderteken", "ondertekenen", "ondertekend", "ondertekening", "sign", "signed", "signature"),
    "consumer": ("afnemer", "consumer", "buyer", "customer", "purchaser", "recipient", "taker"),
    "take": ("neem", "afnemen", "afname", "afgenomen", "consume", "take", "purchase", "acquire", "obtain",
             "use"),
    "admission": ("toetreding", "toegetreden", "toetreden", "toetredings", "admission", "admitted", "join",
                  "joined", "joining", "accession", "enter", "entry"),
    "exit": ("uittreding", "uitgetreden", "uittreden", "exit", "withdraw", "withdrawn", "leave", "left"),
    "form": ("formulier", "toetredingsformulier", "form", "application"),
    "payment": ("betaling", "betalingsverplichting", "betaald", "betalen", "voldaan", "payment", "paid", "pay",
                "deelnemersbijdrage", "fee", "contribution"),
    "obstacle": ("belemmering", "obstacle", "impediment", "barrier", "objection"),
    "legal": ("juridische", "juridisch", "legal", "wettelijke", "wettelijk", "statutory"),
    "commons": ("commons",),
    "sufficient": ("genoeg", "voldoende", "sufficient", "enough"),
    "budget": ("budget", "saldo", "balance", "eurowaarde", "allowance", "quota"),
    "price": ("prijs", "afgesproken", "price", "agreed", "amount", "bedrag", "waarde", "value"),
    "costs": ("kosten", "costs", "cost", "pdx", "voorzieningen", "facilities", "charges"),
    "commercial": ("commercieel", "commerciele", "commercial", "commercially"),
    "non": ("niet", "non", "not", "no"),
    "law": ("wet", "regelgeving", "law", "laws", "regulation", "regulations", "legislation", "strijd",
            "strijdig", "conflict", "lawful"),
    "holder": ("houder", "holder", "owner", "rightsholder"),
    "approval": ("goedgekeurd", "goedkeuring", "approved", "approval", "permission", "toestemming", "consent",
                 "consented"),
    "data": ("data", "dataset", "datasets", "databestanden", "gegevens", "persoonsgegevens"),
    "product": ("pdd", "product", "products", "dienst", "diensten", "service", "services"),
    "catalogue": ("catalogus", "dienstencatalogus", "catalogue", "catalog", "pdc"),
    "publish": ("publicatie", "publiceren", "gepubliceerd", "publish", "published", "publication", "list"),
    "council": ("ecoraad", "council", "board", "authority", "ecosystem", "ecosysteem", "dmi"),
    "year": ("jaar", "jaarlijks", "year", "yearly", "annual", "annually"),
    "processing": ("verwerking", "verwerken", "processing", "process", "verwerkingsactie", "action"),
    "controller": ("verwerkingsverantwoordelijke", "controller"),
    "processor": ("verwerker", "processor"),
    "subject": ("betrokkene", "subject", "person"),
    "purpose": ("doel", "doeleinde", "doeleinden", "purpose", "purposes", "doelbinding"),
    "basis": ("grondslag", "grondslagen", "basis", "ground", "grounds"),
    "vital": ("vitale", "vital"),
    "informed": ("geinformeerd", "informed", "inform", "information", "transparency", "transparent"),
    "commercial_use": ("aanwending", "usage", "gebruik"),
    "open": ("open",),
    "public": ("publiek", "publieke", "public", "publicly"),
}
_CANON = {tok: key for key, toks in GLOSSARY.items() for tok in toks}
_STOP = {"van", "de", "het", "een", "als", "met", "voor", "op", "in", "of", "en", "is", "aan", "bij", "door",
         "the", "a", "an", "to", "for", "by", "has", "heeft", "be", "on", "and", "with", "iets", "ooit", "lijn",
         "artikel", "article", "art", "moet", "must", "may", "mag", "can", "zeven", "twee", "drie", "alle",
         "all", "alleen", "only", "over", "per", "maak", "make", "af", "do", "doe"}
# Type-population plumbing such as ``Extend Fact [Deelnemer] Derived from (Foreach f: f.x)``.
_POPULATION = re.compile(r"^\(?\s*Foreach\s+[\w\[\]-]+\s*:\s*[\w\[\]-]+\.[\w\[\]-]+\s*\)?$")
KIND_GROUPS = {"Fact": "fact", "Bool": "fact", "Event": "act", "Act": "act", "Duty": "duty",
               "Invariant": "fact", "Predicate": "fact", "Placeholder": "param"}


def tokens(name: str) -> list[str]:
    """Canonical tokens of an identifier (``reactie-in-lijn-met-artikel-zeven-twee`` -> ...)."""
    raw = re.split(r"[^0-9a-zA-Zà-ÿ]+", re.sub(r"([a-z])([A-Z])", r"\1-\2", name).lower())
    out = []
    for tok in raw:
        tok = tok.replace("é", "e").replace("ë", "e").replace("ï", "i")
        if not tok or tok in _STOP or tok.isdigit():
            continue
        out.append(_CANON.get(tok) or _prefix_canon(tok) or tok)
    return out


def _prefix_canon(tok: str) -> str | None:
    if len(tok) < 5:
        return None
    for surface, key in _CANON.items():
        if len(surface) >= 5 and (tok.startswith(surface) or surface.startswith(tok)):
            return key
    return None


def _tok_eq(a: str, b: str) -> bool:
    return a == b or (min(len(a), len(b)) >= 5 and (a.startswith(b) or b.startswith(a)))


def similarity(a: str, b: str) -> float:
    """Dice coefficient over canonical tokens (0..1)."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    used: set[int] = set()
    hits = 0
    for x in ta:
        for j, y in enumerate(tb):
            if j not in used and _tok_eq(x, y):
                used.add(j)
                hits += 1
                break
    return 2 * hits / (len(ta) + len(tb))


# --- vocabularies -----------------------------------------------------------------------------------


@dataclass
class Item:
    """One declaration on either side, reduced to what the comparison needs."""

    kind: str                 # fact | act | duty
    name: str
    refs: set[str] = field(default_factory=set)     # identifiers its conditions mention
    source: str = ""          # citation (generated side)


def _expr_refs(expr: str | None) -> set[str]:
    if not expr:
        return set()
    return {m for m in re.findall(r"[A-Za-z_][\w-]*", re.sub(r'"[^"]*"', " ", expr))
            if m not in {"Exists", "Forall", "Not", "Holds", "Enabled", "Violated", "True", "False", "When"}}


def generated_items(spec: EflintSpec) -> list[Item]:
    items = [Item("fact", f.name, _expr_refs(f.holds_when), f.source) for f in spec.facts]
    items += [Item("act", a.name, _expr_refs(a.holds_when), a.source) for a in spec.acts]
    items += [Item("duty", d.name, _expr_refs(d.violated_when), d.source) for d in spec.duties]
    return items


def is_population(decl: Declaration) -> bool:
    return decl.extend and decl.kind == "Fact" and bool(decl.clauses) and all(
        c.keyword == "Derived from" and _POPULATION.match(c.text) for c in decl.clauses)


@dataclass
class Hook:
    """A reference norm attached to an act: ``target`` may only happen when ``refs`` hold."""

    target: str
    module: str
    refs: list[str]


def reference_units(decls: list[Declaration], declared: set[str]) -> tuple[list[Item], list[Hook]]:
    concepts: list[Item] = []
    hooks: list[Hook] = []
    for d in decls:
        if is_population(d) or d.kind == "Placeholder":
            continue
        if d.kind in ("Act", "Event") or (d.extend and d.kind in ("Act", "Event")):
            for c in d.conditions:
                refs = [r for r in c.refs if r in declared and r != d.name]
                if refs:
                    hooks.append(Hook(d.name, d.module, refs))
        if not d.extend:
            refs = {r for c in d.conditions for r in c.refs}
            concepts.append(Item(KIND_GROUPS.get(d.kind, "fact"), d.name, refs))
    return concepts, hooks


# --- comparison ---------------------------------------------------------------------------------------


@dataclass
class Match:
    reference: str
    generated: str | None
    score: float
    kind: str


@dataclass
class Comparison:
    concept_matches: list[Match]
    hook_matches: list[Match]
    domain_matches: list[Match]
    precision_matched: int
    generated_total: int
    articles_cited: list[str]
    articles_missing: list[str]
    counts: dict

    @staticmethod
    def _recall(matches: list[Match]) -> float | None:
        return round(sum(m.generated is not None for m in matches) / len(matches), 3) if matches else None

    def summary(self) -> dict:
        duties = [m for m in self.concept_matches if m.kind == "duty"]
        n_art = len(self.articles_cited) + len(self.articles_missing)
        return {
            "concept_recall": self._recall(self.concept_matches),
            "duty_recall": self._recall(duties),
            "hook_recall": self._recall(self.hook_matches),
            "domain_recall": self._recall(self.domain_matches),
            "precision": round(self.precision_matched / self.generated_total, 3) if self.generated_total else None,
            "article_coverage": round(len(self.articles_cited) / n_art, 3) if n_art else None,
            "articles_missing": self.articles_missing,
            "counts": self.counts,
        }

    def details(self) -> dict:
        def rows(ms):
            return [{"reference": m.reference, "generated": m.generated, "score": round(m.score, 2), "kind": m.kind}
                    for m in ms]
        return {"concepts": rows(self.concept_matches), "hooks": rows(self.hook_matches),
                "domain": rows(self.domain_matches)}


def _best(name: str, kind: str | None, items: list[Item]) -> tuple[Item | None, float]:
    best, score = None, 0.0
    for it in items:
        if kind is not None and it.kind != kind:
            continue
        s = similarity(name, it.name)
        if s > score:
            best, score = it, s
    return (best, score) if score >= MATCH_THRESHOLD else (None, score)


def article_pattern(article: str) -> re.Pattern:
    """How a citation of ``article`` looks: ``7.13``, ``art. 2.3(c)``, ``AVG 6(1)`` / ``6.1`` / ``6 lid 1``."""
    if article.startswith("AVG "):
        num = article[4:]
        m = re.match(r"(\d+)(?:\((\d+)\))?(?:\(([a-z])\))?$", num)
        art, lid, sub = m.groups()
        parts = [rf"{art}\s*\(\s*{lid}\s*\)", rf"\b{art}\.{lid}\b", rf"\b{art}\s+lid\s+{lid}\b"] if lid else [rf"\b{art}\b"]
        alt = "|".join(parts)
        return re.compile(rf"(?:AVG|GDPR|2016/679|Art(?:ikel|icle)?\.?)[^;]*?(?:{alt})", re.I)
    m = re.match(r"(\d+)\.(\d+)([a-z])?$", article)
    art, lid, sub = m.groups()
    suffix = rf"\s*\(?\s*{sub}\b" if sub else r"(?![\d])"
    return re.compile(rf"(?<![\d.]){art}\.{lid}{suffix}", re.I)


def compare(spec: EflintSpec, reference: dict[str, str], *, base: dict[str, str] | None = None,
            articles: list[str] | None = None) -> Comparison:
    """``reference``: the modules formalising the cited articles; ``base``: the domain model they
    build on (e.g. ``artikel1/*``) — only the base concepts the reference refers to are expected."""
    ref_decls = scan_files(reference)
    base_decls = scan_files(base or {})
    declared = {d.name for d in (*ref_decls, *base_decls) if not d.extend}
    concepts, hooks = reference_units(ref_decls, declared)
    gen = generated_items(spec)

    concept_matches = []
    for c in concepts:
        it, s = _best(c.name, c.kind, gen)
        concept_matches.append(Match(c.name, it.name if it else None, s, c.kind))

    acts = [g for g in gen if g.kind == "act"]
    hook_matches = []
    for h in hooks:
        act, s = _best(h.target, "act", acts)
        ok = None
        if act is not None:
            # the act's precondition must mention a counterpart of something the hook refers to
            cond = {r for r in act.refs}
            for r in h.refs:
                if any(similarity(r, c) >= MATCH_THRESHOLD for c in cond):
                    ok = act.name
                    break
            # or refer to a generated fact/duty that counterparts one of the hook's references
            if ok is None:
                for r in h.refs:
                    peer, _ = _best(r, None, [g for g in gen if g.name in cond])
                    if peer is not None:
                        ok = act.name
                        break
        hook_matches.append(Match(f"{h.target} <- {', '.join(h.refs)}", ok, s, "hook"))

    used_base = {r for d in ref_decls for c in d.clauses for r in c.refs} | {p for d in ref_decls for p in d.params}
    used_base |= {d.name for d in ref_decls if d.extend}
    base_concepts = [d for d in base_decls if not d.extend and d.name in used_base and d.kind != "Placeholder"
                     and d.name not in {"id", "int", "datum", "jaar"}]
    domain_matches = []
    seen = set()
    for d in base_concepts:
        if d.name in seen:
            continue
        seen.add(d.name)
        it, s = _best(d.name, None, gen)
        domain_matches.append(Match(d.name, it.name if it else None, s, KIND_GROUPS.get(d.kind, "fact")))

    every = [c.name for c in concepts] + [d.name for d in base_decls if not d.extend]
    precision_matched = sum(1 for g in gen if any(similarity(g.name, r) >= MATCH_THRESHOLD for r in every))

    cited, missing = [], []
    sources = " ; ".join(g.source for g in gen)
    for a in articles or []:
        (cited if article_pattern(a).search(sources) else missing).append(a)

    counts = {
        "generated": {k: sum(1 for g in gen if g.kind == k) for k in ("fact", "act", "duty")},
        "reference": {k: sum(1 for c in concepts if c.kind == k) for k in ("fact", "act", "duty")},
        "reference_hooks": len(hooks),
    }
    return Comparison(concept_matches, hook_matches, domain_matches, precision_matched, len(gen),
                      cited, missing, counts)
