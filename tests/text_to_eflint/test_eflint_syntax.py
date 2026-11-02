"""Parser/printer for the supported eFLINT subset."""
import pytest

from agents.text_to_eflint.generation.syntax import (
    App, BinOp, EflintSyntaxError, Not, Quant, Var, Wrap, parse_expr, parse_program, parse_term, to_text,
)


def test_expression_precedence_and_round_trip():
    e = parse_expr("a(x) && (b(x) || !c()) && Exists y : d(x, y)")
    assert isinstance(e, BinOp) and e.op == "&&"
    assert to_text(e) == "a(x) && (b(x) || !c()) && (Exists y : d(x, y))"
    assert parse_expr(to_text(e)) == e


def test_kebab_names_literals_and_wrappers():
    e = parse_expr('Holds(personal-data-collection("company-a", 1, data-subject))')
    assert isinstance(e, Wrap) and e.kind == "Holds"
    assert e.term.args[0].value == "company-a" and e.term.args[1].value == 1
    assert isinstance(e.term.args[2], Var) and e.term.args[2].name == "data-subject"
    assert isinstance(parse_expr("!Enabled(a())"), Not)
    assert isinstance(parse_expr("(Forall x : f(x))"), Quant)


@pytest.mark.parametrize("bad", ["a(x", "a(x) &&", "Count(x)", "a(x) b(y)", "x + 1"])
def test_bad_expressions_raise(bad):
    with pytest.raises(EflintSyntaxError):
        parse_expr(bad)


def test_parse_term_requires_application():
    assert parse_term('f("a")') == App("f", (parse_expr('"a"'),))
    with pytest.raises(EflintSyntaxError):
        parse_term("x")


def test_program_with_declarations_statements_and_comments():
    src = '''
    // Ontology
    Fact person Identified by String.
    Fact age Identified by Int.
    // Art. 1 - adult
    Fact adult Identified by person * age Holds when age >= 18.
    Act register Actor person Related to age
        Holds when adult()   // trailing
        Creates adult(), adult(person, age).
    Duty pay Holder person Claimant person2 Violated when True.
    +person("a"). -person("a").
    register("a", 20).
    ?!Holds(adult("a", 20)). // False
    '''
    prog = parse_program(src)
    kinds = [s.kind for s in prog.statements]
    assert kinds == ["decl"] * 5 + ["assert", "retract", "perform", "query"]
    adult = prog.statements[2]
    assert adult.doc == "Art. 1 - adult" and adult.decl.identified_by == ["person", "age"]
    reg = prog.statements[3].decl
    assert reg.actor == "person" and reg.related_to == ["age"] and len(reg.creates) == 2
    assert prog.statements[4].decl.claimant == "person2"
    assert prog.statements[-1].trailing == "False"


@pytest.mark.parametrize("bad", ["Event e.", "Fact f Identified by 1..10.", "Fact f Derived from g().", "Act a.",
                                 "Fact f Identified by String"])
def test_unsupported_programs_raise(bad):
    with pytest.raises(EflintSyntaxError):
        parse_program(bad)


def test_require_needs_resolver_and_resolves_once():
    files = {"A.eflint": "Fact a.", "B.eflint": '#require "A.eflint".\nFact b.'}
    with pytest.raises(EflintSyntaxError):
        parse_program('#require "A.eflint".')
    prog = parse_program('#require "A.eflint"\n#require "B.eflint".\nFact c.',
                         resolver=lambda p, _f: (files[p], p))
    assert [d.name for d in prog.decls] == ["a", "b", "c"]
