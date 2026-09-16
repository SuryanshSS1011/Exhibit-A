"""Which merged pull requests a corpus admits, and why the behaviour-preserving rule is strict.

The fix corpora ask how often the engine can prove a real regression. The
behaviour-preserving corpus asks the opposite: given a change that declares it changed
nothing, how often does the engine speak anyway? Every flip it proves there is a candidate
false conviction, so a pull request that quietly does change behaviour would be counted
against the engine for being right. The rule is therefore biased toward rejecting.
"""

from __future__ import annotations

import pytest

from exhibit_a.studies.fix_corpus import CANDIDATE_RULES

FIX = CANDIDATE_RULES["fix"]
PRESERVING = CANDIDATE_RULES["behavior_preserving"]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("fix: clamp pagination args", ["fix_title_prefix"]),
        ("Fixed the resolver", ["fix_title_prefix"]),
        ("fixes a crash on startup", ["fix_title_prefix"]),
        ("refactor: extract the resolver", []),
        ("Add streaming support", []),
    ],
)
def test_the_fix_rule_is_unchanged(title: str, expected: list[str]):
    # Corpora v1 through v8 were selected with this predicate; it must keep selecting the
    # same pull requests or the series stops being comparable.
    assert FIX.basis(title, "", []) == expected


def test_an_exact_bug_label_still_qualifies_a_fix():
    assert FIX.basis("Anything at all", "", [{"name": "Bug"}]) == ["exact_bug_label"]


@pytest.mark.parametrize(
    "title",
    [
        "refactor: extract the resolver",
        "Rename stock_for to lookup_stock",
        "cleanup: drop the dead branch",
        "clean up the executor",
        "tidy: sort the imports",
        "style: reformat with the new line length",
    ],
)
def test_a_declared_refactor_qualifies(title: str):
    assert PRESERVING.basis(title, "", []) == ["refactor_title_prefix"]


def test_a_body_can_declare_it_instead_of_the_title():
    assert PRESERVING.basis(
        "Update the resolver", "This is a pure refactor with no behavior change.", []
    ) == ["declared_no_functional_change"]


@pytest.mark.parametrize(
    ("title", "body", "why"),
    [
        ("refactor: extract resolver and fix the crash", "", "second clause announces a fix"),
        ("refactor: tidy imports", "Fixes #123", "body closes a bug"),
        ("refactor: drop the regression harness", "", "mentions a regression"),
        ("cleanup: remove the bug workaround", "", "mentions a bug"),
        ("fix: clamp pagination args", "", "is a fix"),
        ("chore: bump dependencies", "", "chore moves behaviour freely"),
        ("Add streaming support", "", "a feature"),
    ],
)
def test_anything_hinting_at_a_behaviour_change_is_rejected(title: str, body: str, why: str):
    assert PRESERVING.basis(title, body, []) == [], why


def test_a_refactor_labelled_as_a_bug_is_rejected():
    assert PRESERVING.basis("refactor: tidy imports", "", [{"name": "bug"}]) == []


def test_ordinary_words_containing_fix_do_not_reject():
    # "prefix" and "suffix" have no word boundary before "fix".
    assert PRESERVING.basis("refactor: prefix the suffix helpers", "", []) == [
        "refactor_title_prefix"
    ]


def test_each_rule_states_what_its_corpus_is_asking():
    # The subject field names are shared across rules, so a manifest has to say what a
    # revision pair means under this one rather than leave it implied.
    for rule in CANDIDATE_RULES.values():
        assert rule.question and rule.subject_meaning
    assert "false conviction" in PRESERVING.subject_meaning
