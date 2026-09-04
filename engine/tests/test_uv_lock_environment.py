"""uv lockfiles are a complete, hash-bearing resolution, so the sandbox can use them.

Measured against the fix-coverage pilot frame, 71 of 490 repositories (14.5%) ship a
`uv.lock` and no other supported lockfile. They were being refused as undiscoverable
environments while carrying more pinning information than any format already accepted.
"""

from __future__ import annotations

import textwrap
import subprocess
from pathlib import Path

import pytest

from exhibit_a.executor.base import EnvironmentSetupError, RepoState
from exhibit_a.executor.docker_exec import (
    _carries_hashes,
    _environment_spec,
    _requirements_from_uv,
)
from exhibit_a.studies.fix_corpus import _validate_environment_tree

BASE = "python@sha256:" + "a" * 64
WHEEL = "sha256:" + "1" * 64
SDIST = "sha256:" + "2" * 64


def _lock(body: str) -> str:
    return textwrap.dedent(body).strip() + "\n"


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "uv.lock"
    path.write_text(_lock(body))
    return path


REGISTRY_PACKAGE = f"""
version = 1
requires-python = ">=3.11"

[[package]]
name = "certifi"
version = "2026.1.1"
source = {{ registry = "https://pypi.org/simple" }}
sdist = {{ url = "https://example.invalid/certifi.tar.gz", hash = "{SDIST}" }}
wheels = [
    {{ url = "https://example.invalid/certifi.whl", hash = "{WHEEL}" }},
]
"""


def test_a_registry_package_becomes_a_pinned_hashed_requirement(tmp_path: Path):
    text = _requirements_from_uv(_write(tmp_path, REGISTRY_PACKAGE))

    assert text.startswith("certifi==2026.1.1")
    assert f"--hash={WHEEL}" in text
    assert f"--hash={SDIST}" in text
    assert _carries_hashes(text), "hashes must engage --require-hashes at build time"


def test_the_project_itself_is_skipped_not_installed(tmp_path: Path):
    """An editable or virtual entry is the checkout under test, not a dependency."""
    text = _requirements_from_uv(
        _write(
            tmp_path,
            REGISTRY_PACKAGE
            + """
[[package]]
name = "the-project"
version = "0.1.0"
source = { editable = "." }
""",
        )
    )

    assert "the-project" not in text
    assert "certifi==2026.1.1" in text


@pytest.mark.parametrize("source", ['git = "https://example.invalid/x.git"', 'url = "https://x"'])
def test_a_package_that_is_not_from_an_index_is_refused(tmp_path: Path, source: str):
    """Reproducing a build from an index cannot install something the index does not hold."""
    with pytest.raises(EnvironmentSetupError, match="cannot be reproduced from an index"):
        _requirements_from_uv(
            _write(
                tmp_path,
                REGISTRY_PACKAGE
                + f"""
[[package]]
name = "vendored"
version = "1.0.0"
source = {{ {source} }}
""",
            )
        )


def test_a_package_without_a_hash_is_refused(tmp_path: Path):
    with pytest.raises(EnvironmentSetupError, match="carries no artifact hash"):
        _requirements_from_uv(
            _write(
                tmp_path,
                """
version = 1

[[package]]
name = "nohash"
version = "1.0.0"
source = { registry = "https://pypi.org/simple" }
""",
            )
        )


def test_a_package_without_a_version_is_refused(tmp_path: Path):
    with pytest.raises(EnvironmentSetupError, match="unpinned package"):
        _requirements_from_uv(
            _write(
                tmp_path,
                f"""
version = 1

[[package]]
name = "noversion"
source = {{ registry = "https://pypi.org/simple" }}
wheels = [{{ url = "https://x", hash = "{WHEEL}" }}]
""",
            )
        )


def test_an_environment_marker_is_preserved(tmp_path: Path):
    text = _requirements_from_uv(
        _write(
            tmp_path,
            f"""
version = 1

[[package]]
name = "winonly"
version = "1.0.0"
source = {{ registry = "https://pypi.org/simple" }}
marker = "sys_platform == 'win32'"
wheels = [{{ url = "https://x", hash = "{WHEEL}" }}]
""",
        )
    )

    # Parenthesised so it composes correctly when combined with other conditions.
    assert "winonly==1.0.0 ; (sys_platform == 'win32')" in text


def test_a_lockfile_with_only_the_project_is_refused(tmp_path: Path):
    with pytest.raises(EnvironmentSetupError, match="no installable dependencies"):
        _requirements_from_uv(
            _write(
                tmp_path,
                """
version = 1

[[package]]
name = "the-project"
version = "0.1.0"
source = { virtual = "." }
""",
            )
        )


def test_a_repository_with_only_a_uv_lock_is_now_eligible(tmp_path: Path):
    (tmp_path / "uv.lock").write_text(_lock(REGISTRY_PACKAGE))

    spec = _environment_spec(RepoState(str(tmp_path), "target", source="r"), base_reference=BASE)

    assert spec.image.startswith("exhibit-a-env:")
    assert "certifi==2026.1.1" in spec.requirements[0]


def test_fix_coverage_selector_copies_a_uv_lock_from_the_git_tree(tmp_path: Path):
    """Selection must exercise the same lockfile support as execution."""
    (tmp_path / "uv.lock").write_text(_lock(REGISTRY_PACKAGE))
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "uv.lock"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Exhibit A",
            "-c",
            "user.email=exhibit-a@example.invalid",
            "commit",
            "-qm",
            "Add lock",
        ],
        check=True,
    )
    revision = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    _validate_environment_tree(tmp_path, revision, "https://example.invalid/repo")


def test_uv_lock_wins_over_a_looser_lockfile(tmp_path: Path):
    """It is the only supported format that pins artifacts rather than versions."""
    (tmp_path / "uv.lock").write_text(_lock(REGISTRY_PACKAGE))
    (tmp_path / "requirements.txt").write_text("requests==2.32.4\n")

    spec = _environment_spec(RepoState(str(tmp_path), "target", source="r"), base_reference=BASE)

    assert "certifi==2026.1.1" in spec.requirements[0]
    assert "requests" not in spec.requirements[0]


def test_the_missing_lockfile_message_names_uv(tmp_path: Path):
    with pytest.raises(EnvironmentSetupError, match="no uv.lock, poetry.lock"):
        _environment_spec(RepoState(str(tmp_path), "target", source="r"), base_reference=BASE)


def _pkg(name: str, version: str = "1.0.0", **extra: str) -> str:
    """One registry package entry, with a hash so it is installable."""
    body = (
        f'\n[[package]]\nname = "{name}"\nversion = "{version}"\n'
        f'source = {{ registry = "https://pypi.org/simple" }}\n'
        f'wheels = [{{ url = "https://x", hash = "{WHEEL}" }}]\n'
    )
    return body + "".join(extra.values())


ROOT = 'version = 1\n\n[[package]]\nname = "proj"\nversion = "0.1.0"\nsource = { virtual = "." }\n'


def _requirement(text: str, name: str) -> str | None:
    for line in text.splitlines():
        if line.startswith(f"{name}=="):
            return line
    return None


def test_a_marker_on_the_edge_reaches_the_package_it_gates(tmp_path: Path):
    """The condition lives on the dependency edge, not on the package entry."""
    lock = (
        ROOT
        + 'dependencies = [{ name = "gated", marker = "python_full_version >= \'3.13\'" }]\n'
        + _pkg("gated")
    )
    text = _requirements_from_uv(_write(tmp_path, lock))

    assert "gated==1.0.0 ; (python_full_version >= '3.13')" in _requirement(text, "gated")


def test_a_condition_propagates_through_an_unmarked_edge(tmp_path: Path):
    """This is the audioop-lts shape: the gate is an ancestor, not the direct parent."""
    lock = (
        ROOT
        + 'dependencies = [{ name = "mid", marker = "python_full_version >= \'3.13\'" }]\n'
        + _pkg("mid", extra_deps='dependencies = [{ name = "leaf" }]\n')
        + _pkg("leaf")
    )
    text = _requirements_from_uv(_write(tmp_path, lock))

    assert "python_full_version >= '3.13'" in _requirement(text, "leaf")


def test_an_unconditional_path_beats_a_conditional_one(tmp_path: Path):
    lock = (
        ROOT + "dependencies = [\n"
        '  { name = "shared" },\n'
        '  { name = "mid", marker = "sys_platform == \'win32\'" },\n'
        "]\n" + _pkg("mid", extra_deps='dependencies = [{ name = "shared" }]\n') + _pkg("shared")
    )
    text = _requirements_from_uv(_write(tmp_path, lock))

    assert _requirement(text, "shared").startswith("shared==1.0.0 \\")
    assert " ; " not in _requirement(text, "shared")


def test_resolution_markers_keep_two_versions_of_one_package_apart(tmp_path: Path):
    """Without these, pip is asked to install both versions and refuses."""
    lock = (
        ROOT
        + 'dependencies = [{ name = "av" }]\n'
        + _pkg("av", "17.1.0", rm="resolution-markers = [\"python_full_version < '3.11'\"]\n")
        + _pkg("av", "18.1.0", rm="resolution-markers = [\"python_full_version >= '3.11'\"]\n")
    )
    text = _requirements_from_uv(_write(tmp_path, lock))
    lines = [line for line in text.splitlines() if line.startswith("av==")]

    assert len(lines) == 2
    assert all(" ; " in line for line in lines), "each version must be gated"
    assert "python_full_version < '3.11'" in lines[0]
    assert "python_full_version >= '3.11'" in lines[1]


def test_a_package_no_path_reaches_is_dropped(tmp_path: Path):
    """Lockfiles carry entries resolved for other platforms; installing them is wrong."""
    lock = ROOT + 'dependencies = [{ name = "used" }]\n' + _pkg("used") + _pkg("orphan")
    text = _requirements_from_uv(_write(tmp_path, lock))

    assert _requirement(text, "used") is not None
    assert _requirement(text, "orphan") is None


def test_a_dependency_cycle_terminates(tmp_path: Path):
    lock = (
        ROOT
        + 'dependencies = [{ name = "a" }]\n'
        + _pkg("a", extra_deps='dependencies = [{ name = "b" }]\n')
        + _pkg("b", extra_deps='dependencies = [{ name = "a" }]\n')
    )
    text = _requirements_from_uv(_write(tmp_path, lock))

    assert _requirement(text, "a") is not None
    assert _requirement(text, "b") is not None


def test_a_lockfile_with_no_workspace_root_keeps_every_package(tmp_path: Path):
    """An unfamiliar shape must not silently empty the environment."""
    lock = "version = 1\n" + _pkg("one") + _pkg("two")
    text = _requirements_from_uv(_write(tmp_path, lock))

    assert _requirement(text, "one") is not None
    assert _requirement(text, "two") is not None
