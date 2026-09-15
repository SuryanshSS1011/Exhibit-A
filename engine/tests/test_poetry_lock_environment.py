"""Turning a poetry.lock into a pinned requirements file.

Poetry's lock format has been through renames that a reader can silently no-op on:
`category` became a `groups` list and `marker` became `markers`. Both spellings are
absent from a modern lockfile, so a filter reading only the old one admits everything
it was written to exclude, and says nothing about it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from exhibit_a.executor.base import EnvironmentSetupError
from exhibit_a.executor.docker_exec import _requirements_from_poetry


def _lock(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "poetry.lock"
    path.write_text(body)
    return path


def test_a_platform_marker_survives_into_the_requirements(tmp_path: Path):
    # mem0's lockfile pins pywin32 under platform_system == "Windows". Dropping that
    # marker installed it into a Linux container, and the environment failed on
    # "No matching distribution found for pywin32==310" -- which reads as a broken pin.
    path = _lock(
        tmp_path,
        '[[package]]\nname = "pywin32"\nversion = "310"\noptional = false\n'
        'groups = ["main"]\nmarkers = "platform_system == \\"Windows\\""\n\n'
        '[[package]]\nname = "requests"\nversion = "2.32.4"\noptional = false\n'
        'groups = ["main"]\n',
    )

    lines = _requirements_from_poetry(path).splitlines()

    assert 'pywin32==310; platform_system == "Windows"' in lines
    assert "requests==2.32.4" in lines


def test_the_superseded_singular_spelling_still_works(tmp_path: Path):
    # Older lockfiles are still out there and still valid.
    path = _lock(
        tmp_path,
        '[[package]]\nname = "pywin32"\nversion = "310"\noptional = false\n'
        'category = "main"\nmarker = "sys_platform == \\"win32\\""\n',
    )

    assert _requirements_from_poetry(path).strip() == ('pywin32==310; sys_platform == "win32"')


def test_development_dependencies_are_excluded_under_either_spelling(tmp_path: Path):
    path = _lock(
        tmp_path,
        '[[package]]\nname = "kept"\nversion = "1.0"\noptional = false\ngroups = ["main"]\n\n'
        '[[package]]\nname = "grouped-dev"\nversion = "1.0"\noptional = false\n'
        'groups = ["dev"]\n\n'
        '[[package]]\nname = "categorised-dev"\nversion = "1.0"\noptional = false\n'
        'category = "dev"\n',
    )

    assert _requirements_from_poetry(path).strip() == "kept==1.0"


def test_a_package_in_both_main_and_dev_is_kept(tmp_path: Path):
    # Being needed by a dev group as well does not make it a dev dependency.
    path = _lock(
        tmp_path,
        '[[package]]\nname = "shared"\nversion = "1.0"\noptional = false\n'
        'groups = ["main", "dev"]\n',
    )

    assert _requirements_from_poetry(path).strip() == "shared==1.0"


def test_optional_extras_are_excluded(tmp_path: Path):
    path = _lock(
        tmp_path,
        '[[package]]\nname = "kept"\nversion = "1.0"\noptional = false\ngroups = ["main"]\n\n'
        '[[package]]\nname = "extra-only"\nversion = "1.0"\noptional = true\n'
        'groups = ["main"]\n',
    )

    assert _requirements_from_poetry(path).strip() == "kept==1.0"


def test_an_empty_marker_adds_no_trailing_separator(tmp_path: Path):
    path = _lock(
        tmp_path,
        '[[package]]\nname = "kept"\nversion = "1.0"\noptional = false\n'
        'groups = ["main"]\nmarkers = ""\n',
    )

    assert _requirements_from_poetry(path).strip() == "kept==1.0"


def test_an_unpinned_package_is_refused(tmp_path: Path):
    path = _lock(tmp_path, '[[package]]\nname = "kept"\noptional = false\ngroups = ["main"]\n')

    with pytest.raises(EnvironmentSetupError, match="unpinned"):
        _requirements_from_poetry(path)


def test_a_lockfile_with_no_main_dependencies_is_refused(tmp_path: Path):
    # Better to say so than to build an environment containing nothing.
    path = _lock(
        tmp_path,
        '[[package]]\nname = "dev-only"\nversion = "1.0"\noptional = false\ngroups = ["dev"]\n',
    )

    with pytest.raises(EnvironmentSetupError, match="no installable main dependencies"):
        _requirements_from_poetry(path)


def test_invalid_toml_is_refused(tmp_path: Path):
    with pytest.raises(EnvironmentSetupError, match="invalid poetry.lock"):
        _requirements_from_poetry(_lock(tmp_path, "[[package]\nbroken"))
