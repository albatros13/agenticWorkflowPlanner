"""IR validation and IR -> Petri net compilation, asserted on behaviour (visible traces)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.petri_net_generator.compiler import compile_ir
from agents.petri_net_generator.ir import ProcessIR, check_ir
from agents.petri_net_generator.net_validation import check_soundness, structural_findings, visible_traces

from ._samples import act, ir


def net_of(root: dict):
    return compile_ir(ProcessIR.model_validate(ir(root)))


def traces(root: dict, max_len: int = 8) -> set[tuple[str, ...]]:
    net, im, fm = net_of(root)
    return visible_traces(net, im, fm, max_len=max_len)


def assert_sound_workflow_net(root: dict) -> None:
    net, im, fm = net_of(root)
    assert structural_findings(net, im, fm) == []
    assert check_soundness(net, im, fm, max_states=10_000, timeout_s=5)[0] == "passed"


def test_sequence():
    root = {"kind": "sequence", "steps": [act("submit"), act("validate"), act("approve"), act("finish")]}
    assert traces(root) == {("submit", "validate", "approve", "finish")}
    assert_sound_workflow_net(root)


def test_xor_alternatives_are_mutually_exclusive():
    root = {"kind": "sequence", "steps": [
        act("validate"),
        {"kind": "xor", "branches": [{"condition": "valid", "body": act("approve")},
                                     {"condition": "invalid", "body": act("reject")}]},
    ]}
    # Exactly one branch: never both approve and reject in one case.
    assert traces(root) == {("validate", "approve"), ("validate", "reject")}
    assert_sound_workflow_net(root)


def test_xor_skip_branch():
    root = {"kind": "sequence", "steps": [
        act("a"), {"kind": "xor", "branches": [{"body": act("b")}, {"body": None}]}, act("c")]}
    assert traces(root) == {("a", "b", "c"), ("a", "c")}
    assert_sound_workflow_net(root)


def test_parallel_requires_both_branches_before_continuing():
    root = {"kind": "sequence", "steps": [
        {"kind": "parallel", "branches": [act("x"), act("y")]}, act("after")]}
    # Both interleavings, and "after" only once x and y are done.
    assert traces(root) == {("x", "y", "after"), ("y", "x", "after")}
    assert_sound_workflow_net(root)


def test_loop_can_repeat_and_terminate():
    root = {"kind": "sequence", "steps": [
        act("submit"),
        {"kind": "loop", "body": act("validate"), "redo": act("revise"), "exit_condition": "valid"},
        act("approve"),
    ]}
    t = traces(root, max_len=7)
    assert ("submit", "validate", "approve") in t                                    # terminates at once
    assert ("submit", "validate", "revise", "validate", "approve") in t              # one repetition
    assert ("submit", "validate", "revise", "validate", "revise", "validate", "approve") in t
    assert all(tr[-1] == "approve" and tr.count("validate") == tr.count("revise") + 1 for tr in t)
    assert_sound_workflow_net(root)


def test_nested_constructs_compile_to_a_sound_workflow_net():
    root = {"kind": "sequence", "steps": [
        act("a"),
        {"kind": "parallel", "branches": [
            {"kind": "xor", "branches": [{"body": act("b1")}, {"body": {"kind": "loop", "body": act("b2")}}]},
            {"kind": "sequence", "steps": [act("c1"), act("c2")]},
        ]},
        act("d"),
    ]}
    assert_sound_workflow_net(root)


def test_compilation_is_deterministic():
    root = {"kind": "parallel", "branches": [act("x"), act("y")]}
    names = lambda net: sorted(p.name for p in net.places) + sorted(t.name for t in net.transitions)  # noqa: E731
    assert names(net_of(root)[0]) == names(net_of(root)[0])


@pytest.mark.parametrize("bad", [
    {"kind": "xor", "branches": [{"body": act("only")}]},           # xor needs >= 2 branches
    {"kind": "parallel", "branches": [act("x")]},                    # parallel needs >= 2 branches
    {"kind": "activity", "id": "has space", "label": "x"},           # invalid id
    {"kind": "activity", "id": "a", "label": ""},                     # empty label
    {"kind": "goto", "target": "a"},                                  # unsupported construct
])
def test_invalid_ir_is_rejected_by_schema(bad):
    with pytest.raises(ValidationError):
        ProcessIR.model_validate(ir(bad))


def test_semantic_checks_reject_before_compilation():
    dup = ProcessIR.model_validate(ir({"kind": "sequence", "steps": [act("a"), act("a")]}))
    assert [f.code for f in check_ir(dup, max_nodes=100, max_depth=10)] == ["duplicate_activity_id"]

    big = ProcessIR.model_validate(ir({"kind": "sequence", "steps": [act(f"a{i}") for i in range(20)]}))
    assert "ir_too_large" in {f.code for f in check_ir(big, max_nodes=10, max_depth=10)}

    deep: dict = act("leaf")
    for _ in range(5):
        deep = {"kind": "sequence", "steps": [deep]}
    assert "ir_too_deep" in {f.code for f in check_ir(ProcessIR.model_validate(ir(deep)), max_nodes=100, max_depth=3)}
