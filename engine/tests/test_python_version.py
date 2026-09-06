"""Choosing the interpreter a checkout declares rather than the one we happen to ship."""

from __future__ import annotations

from pathlib import Path

import pytest

from exhibit_a.executor.python_version import (
    DEFAULT,
    declared_python,
    image_for,
    select_python,
)


def test_a_project_that_states_nothing_keeps_the_default():
    # Most projects accept the default, and nothing that works today should move.
    assert select_python(None) is DEFAULT
    assert select_python("") is DEFAULT


@pytest.mark.parametrize(
    "specifier",
    [">=3.10, <4.0", ">=3.11", ">=3.10, <3.14", ">=3.12", ">=3.10", "~=3.11", "!=3.9"],
)
def test_the_default_is_kept_whenever_it_qualifies(specifier: str):
    assert select_python(specifier) == (3, 12)


@pytest.mark.parametrize(
    ("specifier", "expected"),
    [
        (">=3.11, <3.12", (3, 11)),  # serena and private-gpt, verbatim
        ("==3.11.*", (3, 11)),  # what their uv.lock resolved against
        (">=3.13", (3, 13)),  # sentry's lock
        ("<3.12", (3, 11)),
        ("==3.13", (3, 13)),
    ],
)
def test_a_project_that_excludes_the_default_gets_what_it_asked_for(
    specifier: str, expected: tuple[int, int]
):
    # These were installed on 3.12 anyway. Their lockfiles carry wheels tagged for the
    # interpreter they resolved against, so pip matched nothing and fell back to building
    # every binary package from source -- which looks like a missing compiler and is not.
    assert select_python(specifier) == expected


def test_a_project_we_cannot_serve_is_refused_rather_than_substituted():
    # Silently running a project's tests on an interpreter it rejects would produce
    # observations about a configuration the project does not support.
    assert select_python("==3.9.*") is None
    assert select_python("<3.10") is None


def test_an_unparseable_clause_does_not_refuse_the_project():
    # Our gap in specifier support is not the project's problem.
    assert select_python("garbage") is DEFAULT
    assert select_python(">=3.11, something-odd") == (3, 12)


def test_the_lock_is_read_before_pyproject(tmp_path: Path):
    # The lock states the range the resolution was produced for, which is what the wheel
    # tags inside it were built against, and that is what the install has to match.
    (tmp_path / "pyproject.toml").write_text('requires-python = ">=3.10"\n')
    (tmp_path / "uv.lock").write_text('version = 1\nrequires-python = "==3.11.*"\n')

    assert declared_python(tmp_path) == "==3.11.*"
    assert select_python(declared_python(tmp_path)) == (3, 11)


def test_pyproject_is_the_fallback_without_a_lock(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nrequires-python = ">=3.11, <3.12"\n'
    )

    assert declared_python(tmp_path) == ">=3.11, <3.12"


def test_a_checkout_that_declares_nothing_reads_as_none(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')

    assert declared_python(tmp_path) is None


def test_image_names_follow_the_official_slim_tags():
    assert image_for((3, 11)) == "python:3.11-slim"
    assert image_for(DEFAULT) == "python:3.12-slim"
