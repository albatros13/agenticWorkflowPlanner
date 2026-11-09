"""Parser + printer for the eFLINT subset the generator emits (and the reference spec uses).

Supported:

* declarations ``Fact`` (``Identified by String|Int|T1 * T2``, ``Holds when``), ``Act`` (``Actor``,
  ``Recipient``, ``Related to``, ``Holds when``, ``Conditioned by``, ``Creates``, ``Terminates``),
  ``Duty`` (``Holder``, ``Claimant``, ``Related to``, ``Violated when``);
* statements ``#require "file".``, ``+term.``, ``-term.``, ``act(args).``, ``?expr.``;
* expressions ``&&``, ``||``, ``!``, ``==``/``!=``/``<``/``<=``/``>``/``>=``, parentheses,
  ``f(args)`` (``f()`` fills arguments from same-named variables), ``Holds(..)``,
  ``Enabled(..)``, ``Violated(..)``, ``Exists x, y : e``, ``Forall x : e``, ``True``/``False``,
  string and integer literals.

Anything else (``Event``, ``Extend``, ranges, arithmetic, ``Count``/``Sum``, ``Derived from``)
raises :class:`EflintSyntaxError` so unsupported output is caught rather than silently mis-read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Union


class EflintSyntaxError(ValueError):
    def __init__(self, message: str, *, line: int | None = None, source: str = ""):
        where = f"{source}:{line}: " if line is not None else ""
        super().__init__(f"{where}{message}")
        self.line = line


# --- tokens ------------------------------------------------------------------------------------

NAME_RE = r"[A-Za-z_][A-Za-z0-9_]*(?:-[A-Za-z0-9_]+)*'*"
_TOKEN = re.compile(
    rf'\s*(?:(?P<str>"(?:[^"\\]|\\.)*")|(?P<int>\d+)|(?P<name>{NAME_RE})'
    r"|(?P<op>&&|\|\||==|!=|<=|>=|[!<>(),:*+\-?.#]))"
)
VALID_NAME = re.compile(rf"^{NAME_RE}$")


@dataclass(frozen=True)
class Tok:
    kind: str  # str | int | name | op
    value: str
    pos: int


def tokenize(text: str) -> list[Tok]:
    out: list[Tok] = []
    i = 0
    while i < len(text):
        if text[i:].strip() == "":
            break
        m = _TOKEN.match(text, i)
        if not m or m.end() == i:
            raise EflintSyntaxError(f"unexpected character {text[i:].lstrip()[:1]!r} in {text.strip()!r}")
        kind = m.lastgroup
        out.append(Tok(kind, m.group(kind), m.start(kind)))
        i = m.end()
    return out


# --- AST ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Lit:
    value: Union[str, int, bool]


@dataclass(frozen=True)
class Var:
    name: str


@dataclass(frozen=True)
class App:
    name: str
    args: tuple  # tuple[Expr, ...]; () means "fill all arguments from scope"


@dataclass(frozen=True)
class Not:
    expr: "Expr"


@dataclass(frozen=True)
class BinOp:
    op: str  # && | ||
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True)
class Cmp:
    op: str
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True)
class Wrap:
    kind: str  # Holds | Enabled | Violated
    term: App


@dataclass(frozen=True)
class Quant:
    kind: str  # Exists | Forall
    vars: tuple[str, ...]
    body: "Expr"


Expr = Union[Lit, Var, App, Not, BinOp, Cmp, Wrap, Quant]

KEYWORDS = {
    "Fact", "Act", "Duty", "Event", "Identified", "by", "Holds", "when", "Actor", "Recipient",
    "Related", "to", "Creates", "Terminates", "Holder", "Claimant", "Violated", "Enabled",
    "Exists", "Forall", "True", "False", "Conditioned", "Derived", "from", "String", "Int",
    "Count", "Sum", "Max", "Min", "Foreach", "When", "Where", "Not", "Present", "Extend",
}
_CMP_OPS = ("==", "!=", "<", "<=", ">", ">=")


class _Parser:
    def __init__(self, toks: list[Tok], text: str):
        self.toks, self.i, self.text = toks, 0, text

    def peek(self, k: int = 0) -> Tok | None:
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else None

    def at(self, value: str, k: int = 0) -> bool:
        t = self.peek(k)
        return t is not None and t.value == value and t.kind in ("op", "name")

    def take(self, value: str | None = None) -> Tok:
        t = self.peek()
        if t is None:
            raise EflintSyntaxError(f"unexpected end of input in {self.text.strip()!r}")
        if value is not None and t.value != value:
            raise EflintSyntaxError(f"expected {value!r} but found {t.value!r} in {self.text.strip()!r}")
        self.i += 1
        return t

    def done(self) -> bool:
        return self.i >= len(self.toks)

    # expr := or
    def expr(self) -> Expr:
        left = self.and_()
        while self.at("||"):
            self.take()
            left = BinOp("||", left, self.and_())
        return left

    def and_(self) -> Expr:
        left = self.cmp()
        while self.at("&&"):
            self.take()
            left = BinOp("&&", left, self.cmp())
        return left

    def cmp(self) -> Expr:
        left = self.unary()
        t = self.peek()
        if t is not None and t.kind == "op" and t.value in _CMP_OPS:
            self.take()
            return Cmp(t.value, left, self.unary())
        return left

    def unary(self) -> Expr:
        if self.at("!"):
            self.take()
            return Not(self.unary())
        return self.primary()

    def primary(self) -> Expr:
        t = self.take()
        if t.kind == "str":
            return Lit(bytes(t.value[1:-1], "utf-8").decode("unicode_escape"))
        if t.kind == "int":
            return Lit(int(t.value))
        if t.value == "(":
            e = self.expr()
            self.take(")")
            return e
        if t.kind != "name":
            raise EflintSyntaxError(f"unexpected {t.value!r} in {self.text.strip()!r}")
        if t.value in ("True", "False"):
            return Lit(t.value == "True")
        if t.value in ("Exists", "Forall"):
            names = [self._name()]
            while self.at(","):
                self.take()
                names.append(self._name())
            self.take(":")
            return Quant(t.value, tuple(names), self.expr())
        if t.value in ("Holds", "Enabled", "Violated") and self.at("("):
            self.take("(")
            inner = self.expr()
            self.take(")")
            if not isinstance(inner, App):
                raise EflintSyntaxError(f"{t.value}(...) expects a term like f(x), got {to_text(inner)!r}")
            return Wrap(t.value, inner)
        if t.value in KEYWORDS:
            raise EflintSyntaxError(f"unsupported keyword {t.value!r} in {self.text.strip()!r}")
        if self.at("("):
            self.take("(")
            args: list[Expr] = []
            if not self.at(")"):
                args.append(self.expr())
                while self.at(","):
                    self.take()
                    args.append(self.expr())
            self.take(")")
            return App(t.value, tuple(args))
        return Var(t.value)

    def _name(self) -> str:
        t = self.take()
        if t.kind != "name" or t.value in KEYWORDS:
            raise EflintSyntaxError(f"expected a type name, found {t.value!r}")
        return t.value


def parse_expr(text: str) -> Expr:
    p = _Parser(tokenize(text), text)
    if p.done():
        raise EflintSyntaxError("empty expression")
    e = p.expr()
    if not p.done():
        raise EflintSyntaxError(f"unexpected {p.peek().value!r} after expression in {text.strip()!r}")
    return e


def parse_term(text: str) -> App:
    e = parse_expr(text)
    if not isinstance(e, App):
        raise EflintSyntaxError(f"expected a term like f(x, y), got {text.strip()!r}")
    return e


# --- printer -----------------------------------------------------------------------------------

_PREC = {"||": 1, "&&": 2}


def _lit(v) -> str:
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, int):
        return str(v)
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def to_text(e: Expr, prec: int = 0) -> str:
    if isinstance(e, Lit):
        return _lit(e.value)
    if isinstance(e, Var):
        return e.name
    if isinstance(e, App):
        return f"{e.name}({', '.join(to_text(a) for a in e.args)})"
    if isinstance(e, Wrap):
        return f"{e.kind}({to_text(e.term)})"
    if isinstance(e, Not):
        return "!" + to_text(e.expr, 9)
    if isinstance(e, Cmp):
        s = f"{to_text(e.left, 5)} {e.op} {to_text(e.right, 5)}"
        return f"({s})" if prec > 3 else s
    if isinstance(e, BinOp):
        p = _PREC[e.op]
        s = f"{to_text(e.left, p)} {e.op} {to_text(e.right, p + 1)}"
        return f"({s})" if prec > p else s
    if isinstance(e, Quant):
        return f"({e.kind} {', '.join(e.vars)} : {to_text(e.body)})"
    raise TypeError(e)


def conjuncts(e: Expr) -> list[Expr]:
    if isinstance(e, BinOp) and e.op == "&&":
        return conjuncts(e.left) + conjuncts(e.right)
    return [e]


def walk(e: Expr):
    yield e
    if isinstance(e, App):
        for a in e.args:
            yield from walk(a)
    elif isinstance(e, Wrap):
        yield from walk(e.term)
    elif isinstance(e, Not):
        yield from walk(e.expr)
    elif isinstance(e, (BinOp, Cmp)):
        yield from walk(e.left)
        yield from walk(e.right)
    elif isinstance(e, Quant):
        yield from walk(e.body)


# --- programs (whole .eflint files) ------------------------------------------------------------

@dataclass
class Decl:
    kind: str  # Fact | Act | Duty
    name: str
    identified_by: list[str] = field(default_factory=list)
    actor: str | None = None
    recipient: str | None = None
    holder: str | None = None
    claimant: str | None = None
    related_to: list[str] = field(default_factory=list)
    holds_when: Expr | None = None
    conditioned_by: Expr | None = None
    violated_when: Expr | None = None
    creates: list[App] = field(default_factory=list)
    terminates: list[App] = field(default_factory=list)


@dataclass
class Stmt:
    kind: str  # decl | require | assert | retract | perform | query
    line: int
    source: str = ""
    doc: str = ""       # comment block directly above
    trailing: str = ""  # comment on the statement's last line
    decl: Decl | None = None
    term: App | None = None
    expr: Expr | None = None
    path: str = ""


@dataclass
class Program:
    statements: list[Stmt] = field(default_factory=list)

    @property
    def decls(self) -> list[Decl]:
        return [s.decl for s in self.statements if s.kind == "decl"]


def _scan(text: str):
    """Single pass: statements ``(text, first_line, last_line)``, full-line and trailing comments."""
    stmts: list[tuple[str, int, int]] = []
    comments: dict[int, str] = {}  # full-line comments
    trailing: dict[int, str] = {}  # comments after code on the same line
    buf: list[str] = []
    start_line = None
    line = 1
    depth = 0
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j == -1 else j
            line_start = text.rfind("\n", 0, i) + 1
            target = comments if text[line_start:i].strip() == "" else trailing
            target[line] = text[i + 2:j].strip()
            i = j
            continue
        if c == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            line += text.count("\n", i, j)
            i = j
            continue
        if c == "\n":
            line += 1
            if buf and "".join(buf).lstrip().startswith("#"):  # directives end at end of line
                stmts.append(("".join(buf).strip().rstrip("."), start_line, line - 1))
                buf, start_line = [], None
            elif buf:
                buf.append(" ")
            i += 1
            continue
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            if start_line is None:
                start_line = line
            buf.append(text[i:j + 1])
            i = j + 1
            continue
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        if c == "." and depth == 0 and (i + 1 >= n or text[i + 1] in " \t\r\n/"):
            if buf and "".join(buf).strip():
                stmts.append(("".join(buf).strip(), start_line, line))
            buf, start_line = [], None
            i += 1
            continue
        if not c.isspace() and start_line is None:
            start_line = line
        if start_line is not None:
            buf.append(c)
        i += 1
    if buf and "".join(buf).strip():
        rest = "".join(buf).strip()
        if rest.startswith("#"):
            stmts.append((rest.rstrip("."), start_line, line))
        else:
            raise EflintSyntaxError(f"statement not terminated by '.': {rest[:60]!r}", line=start_line)
    return stmts, comments, trailing


_CLAUSES = {  # first keyword -> (second keyword or None, clause id)
    "Identified": ("by", "identified_by"),
    "Actor": (None, "actor"),
    "Recipient": (None, "recipient"),
    "Holder": (None, "holder"),
    "Claimant": (None, "claimant"),
    "Related": ("to", "related_to"),
    "Holds": ("when", "holds_when"),
    "Conditioned": ("by", "conditioned_by"),
    "Violated": ("when", "violated_when"),
    "Creates": (None, "creates"),
    "Terminates": (None, "terminates"),
    "Derived": ("from", "derived_from"),
}


def _split_commas(toks: list[Tok]) -> list[list[Tok]]:
    parts, cur, depth = [], [], 0
    for t in toks:
        if t.value == "(":
            depth += 1
        elif t.value == ")":
            depth -= 1
        if t.value == "," and t.kind == "op" and depth == 0:
            parts.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        parts.append(cur)
    return parts


def _slice(text: str, toks: list[Tok]) -> str:
    return text[toks[0].pos: toks[-1].pos + len(toks[-1].value)] if toks else ""


def _parse_decl(text: str) -> Decl:
    toks = tokenize(text)
    kind = toks[0].value
    if kind not in ("Fact", "Act", "Duty"):
        raise EflintSyntaxError(f"unsupported declaration {kind!r} (supported: Fact, Act, Duty)")
    if len(toks) < 2 or toks[1].kind != "name":
        raise EflintSyntaxError(f"{kind} declaration without a name")
    decl = Decl(kind=kind, name=toks[1].value)
    # Split remaining tokens into clauses at depth-0 clause keywords.
    clauses: list[tuple[str, list[Tok]]] = []
    depth, i = 0, 2
    while i < len(toks):
        t = toks[i]
        spec = _CLAUSES.get(t.value) if (t.kind == "name" and depth == 0) else None
        if spec and (spec[0] is None or (i + 1 < len(toks) and toks[i + 1].value == spec[0])):
            clauses.append((spec[1], []))
            i += 1 if spec[0] is None else 2
            continue
        if not clauses:
            raise EflintSyntaxError(f"unexpected {t.value!r} in {kind} {decl.name}")
        if t.value == "(":
            depth += 1
        elif t.value == ")":
            depth -= 1
        clauses[-1][1].append(t)
        i += 1
    for cid, body in clauses:
        src = _slice(text, body)
        if cid == "derived_from":
            raise EflintSyntaxError(f"'Derived from' is not supported ({decl.name}); use 'Holds when'")
        if cid == "identified_by":
            parts = [t.value for t in body if t.value != "*"]
            if not parts or any(t.kind != "name" for t in body if t.value != "*"):
                raise EflintSyntaxError(f"unsupported 'Identified by {src}' in {decl.name}")
            decl.identified_by = parts
        elif cid in ("actor", "recipient", "holder", "claimant"):
            if len(body) != 1 or body[0].kind != "name":
                raise EflintSyntaxError(f"'{cid}' of {decl.name} must be a single type name, got {src!r}")
            setattr(decl, cid, body[0].value)
        elif cid == "related_to":
            decl.related_to = [_slice(text, p) for p in _split_commas(body)]
        elif cid in ("holds_when", "conditioned_by", "violated_when"):
            setattr(decl, cid, parse_expr(src))
        else:  # creates / terminates
            getattr(decl, cid).extend(parse_term(_slice(text, p)) for p in _split_commas(body))
    if kind == "Fact" and not decl.identified_by:
        decl.identified_by = ["String"]
    if kind == "Act" and not decl.actor:
        raise EflintSyntaxError(f"Act {decl.name} has no Actor")
    if kind == "Duty" and not (decl.holder and decl.claimant):
        raise EflintSyntaxError(f"Duty {decl.name} needs a Holder and a Claimant")
    return decl


def _parse_stmt(text: str) -> Stmt:
    if text.startswith("#"):
        m = re.match(r'#(require|include)\s+"([^"]+)"$', text)
        if not m:
            raise EflintSyntaxError(f"unsupported directive {text!r}")
        return Stmt("require", 0, path=m.group(2))
    head = text.split(None, 1)[0]
    if head in ("Fact", "Act", "Duty", "Event", "Extend", "Placeholder", "Predicate", "Invariant"):
        return Stmt("decl", 0, decl=_parse_decl(text))
    if text[0] in "+-":
        return Stmt("assert" if text[0] == "+" else "retract", 0, term=parse_term(text[1:]))
    if text[0] == "?":
        return Stmt("query", 0, expr=parse_expr(text[1:]))
    return Stmt("perform", 0, term=parse_term(text))


Resolver = Callable[[str, str], tuple[str, str]]  # (path, from_source) -> (text, source_id)


def file_resolver(base_dir: Path) -> Resolver:
    """Resolve ``#require`` paths next to the requiring file, falling back to its parent dir."""

    def resolve(path: str, from_source: str) -> tuple[str, str]:
        here = Path(from_source).parent if from_source and from_source != "<input>" else base_dir
        for cand in (here / path, here.parent / path, base_dir / path):
            if cand.is_file():
                return cand.read_text(encoding="utf-8"), str(cand.resolve())
        raise EflintSyntaxError(f"cannot resolve #require {path!r}")

    return resolve


def parse_program(
    text: str, *, source: str = "<input>", resolver: Resolver | None = None, _seen: set | None = None
) -> Program:
    """Parse a whole file; ``#require`` is inlined via ``resolver`` (each file at most once)."""
    seen = _seen if _seen is not None else set()
    seen.add(source)
    raw, comments, trailing = _scan(text)
    prog = Program()
    for body, first, last in raw:
        try:
            st = _parse_stmt(body)
        except EflintSyntaxError as exc:
            raise EflintSyntaxError(str(exc), line=first, source=source) from None
        st.line, st.source = first, source
        doc, ln = [], first - 1
        while ln in comments:
            doc.insert(0, comments[ln])
            ln -= 1
        st.doc = "\n".join(doc)
        st.trailing = trailing.get(last, "")
        if st.kind == "require":
            if resolver is None:
                raise EflintSyntaxError(f"#require {st.path!r} but no resolver given", line=first, source=source)
            sub_text, sub_source = resolver(st.path, source)
            if sub_source not in seen:
                prog.statements.extend(
                    parse_program(sub_text, source=sub_source, resolver=resolver, _seen=seen).statements
                )
            continue
        prog.statements.append(st)
    return prog


def parse_file(path: str | Path) -> Program:
    path = Path(path).resolve()
    return parse_program(path.read_text(encoding="utf-8"), source=str(path), resolver=file_resolver(path.parent))
