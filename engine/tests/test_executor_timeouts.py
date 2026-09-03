"""The wall-clock budget must bind everything a run started, not just its first child.

``executor/base.py`` promises a "hard wall-clock + resource budget per run; timeout =>
a failed run, never a guess". Signalling only the direct child leaves a spawned worker
running on the host, and killing the Docker client leaves the container running on the
daemon, so the promise is kept only when the timeout reaches the whole tree.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from exhibit_a.executor.base import ExecSpec, RepoState
from exhibit_a.executor.docker_exec import DockerExecutor
from exhibit_a.executor.local_exec import LocalExecutor


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_local_timeout_kills_the_spawned_grandchild(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "module.py").write_text("VALUE = 1\n")
    marker = tmp_path / "grandchild.pid"
    (repo / "runner.py").write_text(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )

    outcome = LocalExecutor().run(
        RepoState(path=str(repo), label="target"),
        ExecSpec(
            test_path="test_noop.py",
            test_code="def test_noop():\n    assert True\n",
            command=f"{sys.executable} runner.py",
            timeout_s=2,
        ),
    )

    assert outcome.timed_out
    assert outcome.exit_code == 124
    assert marker.exists(), "runner never started; the test proves nothing"

    pid = int(marker.read_text())
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid), "a process the candidate spawned outlived the budget"


def test_docker_timeout_force_removes_the_named_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "module.py").write_text("VALUE = 1\n")
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        calls.append(argv)
        if argv[1] == "run":
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    outcome = DockerExecutor(base_image="pinned:1").run(
        RepoState(path=str(repo), label="target"),
        ExecSpec(
            test_path="test_noop.py",
            test_code="def test_noop():\n    assert True\n",
            command="python3 -m pytest -q",
            timeout_s=1,
        ),
    )

    assert outcome.timed_out
    assert outcome.exit_code == 124
    run_argv = calls[0]
    container = run_argv[run_argv.index("--name") + 1]
    assert ["docker", "rm", "--force", container] in calls
