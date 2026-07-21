from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from exhibit_a.intake import git_bisect

REPO_URL = "https://github.com/example/project.git"
BAD = "b" * 40
GOOD = "a" * 40
CULPRIT = "c" * 40
PARENT = "d" * 40


def test_bisect_runs_generated_test_in_locked_down_docker(monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str]] = []

    def fake_git(
        argv: list[str], *, timeout: int | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if "clone" in argv:
            Path(argv[-1]).mkdir()
        if "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout=f"{PARENT}\n", stderr="")
        if "run" in argv and "bisect" in argv:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=f"{CULPRIT} is the first bad commit\n",
                stderr="",
            )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(git_bisect, "_run_git", fake_git)

    result = git_bisect.bisect_reproduction(
        REPO_URL,
        bad_sha=BAD,
        good_sha=GOOD,
        test_path="test_repro.py",
        test_code="def test_repro(): assert False\n",
        run_command="python3 -m pytest -x -q test_repro.py",
        image="exhibit-a-env:fixture",
    )

    assert result.culprit == CULPRIT
    assert result.parent == PARENT
    bisect_run = next(call for call in calls if "bisect" in call and "run" in call)
    assert bisect_run[:4] == ["git", "-C", bisect_run[2], "-c"]
    assert "core.hooksPath=/dev/null" in bisect_run
    assert "--network" in bisect_run
    assert bisect_run[bisect_run.index("--network") + 1] == "none"
    assert "--read-only" in bisect_run
    assert bisect_run[-6:] == ["python3", "-m", "pytest", "-x", "-q", "test_repro.py"]


@pytest.mark.parametrize(
    ("bad_sha", "command"),
    [
        ("main; touch /tmp/pwned", "python3 -m pytest -q test_repro.py"),
        (BAD, "python3 -m pytest -q test_repro.py; id"),
        (BAD, "python3 malicious.py test_repro.py"),
    ],
)
def test_bisect_rejects_untrusted_sha_and_commands_before_git(
    monkeypatch: pytest.MonkeyPatch, bad_sha: str, command: str
):
    called = False

    def fake_git(argv: list[str], *, timeout: int | None = None):
        nonlocal called
        called = True

    monkeypatch.setattr(git_bisect, "_run_git", fake_git)

    with pytest.raises(ValueError):
        git_bisect.bisect_reproduction(
            REPO_URL,
            bad_sha=bad_sha,
            good_sha=GOOD,
            test_path="test_repro.py",
            test_code="def test_repro(): assert False\n",
            run_command=command,
            image="exhibit-a-env:fixture",
        )

    assert not called
