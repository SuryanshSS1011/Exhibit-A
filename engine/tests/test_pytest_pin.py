"""The replay pytest version is pinned in exactly one place.

pytest's failure renderer ends up inside the signed log, so the pinned version is part
of what archived evidence means. It was previously repeated as a literal in four modules,
which is a bump that can land partially and leave the judge validating one version while
the image ships another.
"""

from __future__ import annotations

from pathlib import Path

from exhibit_a.eef import _dockerfile as eef_dockerfile
from exhibit_a.executor.docker_exec import _dockerfile as sandbox_dockerfile
from exhibit_a.replay_environment import PINNED_PYTEST_VERSION

ENGINE = Path(__file__).resolve().parents[1] / "exhibit_a"


def test_the_version_literal_appears_in_one_module_only():
    offenders = sorted(
        str(path.relative_to(ENGINE))
        for path in ENGINE.rglob("*.py")
        if path.name != "replay_environment.py" and f'"{PINNED_PYTEST_VERSION}"' in path.read_text()
    )

    assert offenders == [], f"pytest pin duplicated in {offenders}"


def test_both_dockerfiles_install_the_pinned_version():
    replay = eef_dockerfile(["python3", "-m", "pytest", "-q"])
    sandbox = sandbox_dockerfile(["requirements-0.txt"], "python@sha256:" + "a" * 64)

    assert f"pytest=={PINNED_PYTEST_VERSION}" in replay
    assert f"pytest=={PINNED_PYTEST_VERSION}" in sandbox
