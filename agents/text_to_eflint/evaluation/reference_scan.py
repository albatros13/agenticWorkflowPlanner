"""A declaration-level scanner for full eFLINT, for comparing a reference with a generated spec.

The generator's parser (:mod:`..generation.syntax`) accepts only the subset the generator emits.
Reference formalisations such as the DMI Afsprakenstelsel use the full language (``Extend``,
``Derived from``, ``Placeholder``, ``Bool``, ``Invariant``, ``Count``/``Sum``, ``[Bracketed]``
names, primed variables), so they cannot be loaded or simulated by it. This scanner does not
interpret eFLINT; it only recovers *what is declared*:

* every declaration (``Fact``/``Act``/``Duty``/``Event``/``Bool``/``Placeholder``/``Invariant``/
  ``Predicate``), with its ``Identified by`` / actor / recipient / holder / claimant / related
  parameters;
* every clause that adds a norm to a declaration, including ``Extend Act x Conditioned by ...``
  hooks, with the identifiers its expression refers to.

That is enough for the vocabulary/structure comparison in :mod:`.compare`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

KINDS = ("Fact", "Act", "Duty", "Event", "Bool", "Placeholder", "Invariant", "Predicate")
# Clause keywords and the attribute they fill.
CLAUSES = {
    "Identified by": "params", "Actor": "actor", "Recipient": "recipient", "Related to": "related",
    "Holder": "holder", "Claimant": "claimant", "Holds when": "condition", "Conditioned by": "condition",
    "Derived from": "derivation", "Violated when": "violation", "When": "condition",
    "Creates": "creates", "Terminates": "terminates", "For": "params",
}
_HEAD = re.compile(rf"^(?P<extend>Extend\s+)?(?P<kind>{'|'.join(KINDS)})\s+(?P<name>\[[^\]]+\]|[\w'-]+)(?P<rest>.*)$")
_CLAUSE = re.compile(r"\b(" + "|".join(sorted((re.escape(k) for k in CLAUSES), key=len, reverse=True)) + r")\b")
_IDENT = re.compile(r"\[[^\]]+\]|[A-Za-z_][\w-]*")
# Words in expressions that are not references to declarations.
_KEYWORDS = {"Exists", "Forall", "Foreach", "When", "Not", "Count", "Sum", "Max", "Min", "True", "False",
             "Holds", "Enabled", "Violated", "String", "Int", "Present", "Where", "Is", "In"}


@dataclass
class Clause:
    keyword: str
    text: str
    refs: list[str] = field(default_factory=list)  # identifiers mentioned (declarations, params)


@dataclass
class Declaration:
    kind: str
    name: str
    module: str
    extend: bool = False
    params: list[str] = field(default_factory=list)
    clauses: list[Clause] = field(default_factory=list)

    @property
    def conditions(self) -> list[Clause]:
        return [c for c in self.clauses if CLAUSES[c.keyword] in ("condition", "violation", "derivation")]

    @property
    def is_norm(self) -> bool:
        """Adds normative content: a duty, an invariant, or a condition/derivation/violation."""
        return self.kind in ("Duty", "Invariant") or bool(self.conditions)


def strip_comments(text: str) -> str:
    out = []
    for line in text.splitlines():
        in_str, cut = False, None
        for i, ch in enumerate(line):
            if ch == '"':
                in_str = not in_str
            elif not in_str and line.startswith("//", i):
                cut = i
                break
        out.append(line[:cut] if cut is not None else line)
    return "\n".join(out)


def _refs(expr: str) -> list[str]:
    seen: dict[str, None] = {}
    for m in _IDENT.finditer(re.sub(r'"[^"]*"', " ", expr)):
        tok = m.group(0).rstrip("'")
        if tok not in _KEYWORDS and not tok.isdigit():
            seen.setdefault(tok, None)
    return list(seen)


def scan(text: str, module: str = "") -> list[Declaration]:
    """Declarations of one eFLINT module (``#require`` is not followed)."""
    decls: list[Declaration] = []
    current: Declaration | None = None
    body: list[str] = []

    def close():
        if current is None:
            return
        joined = " ".join(" ".join(body).split())
        joined = re.sub(r"\.\s*$", "", joined)
        parts = _CLAUSE.split(joined)
        # parts = [prefix, kw1, text1, kw2, text2, ...]
        for kw, chunk in zip(parts[1::2], parts[2::2], strict=True):
            chunk = chunk.strip().rstrip(".").strip()
            attr = CLAUSES[kw]
            if attr == "params":
                current.params += [p.strip() for p in re.split(r"[*,]", chunk) if p.strip()]
            elif attr in ("actor", "recipient", "holder", "claimant", "related"):
                current.params += [p.strip() for p in chunk.split(",") if p.strip()]
                current.clauses.append(Clause(kw, chunk, _refs(chunk)))
            else:
                current.clauses.append(Clause(kw, chunk, _refs(chunk)))
        decls.append(current)

    for raw in strip_comments(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _HEAD.match(line)
        if m and not raw.startswith((" ", "\t")):
            close()
            current = Declaration(kind=m.group("kind"), name=m.group("name"), module=module,
                                  extend=bool(m.group("extend")))
            body = [m.group("rest")]
        elif current is not None and raw.startswith((" ", "\t")):
            body.append(line)
        else:  # a statement (#require, +fact, act(...), {, }, .) ends the declaration
            close()
            current, body = None, []
    close()
    return decls


def scan_files(sources: dict[str, str]) -> list[Declaration]:
    return [d for module, text in sources.items() for d in scan(text, module)]
