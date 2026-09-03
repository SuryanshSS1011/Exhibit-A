"""End-to-end checks against a real Docker daemon.

The rest of the Docker suite fakes `subprocess.run`, which proves the argv we build but
not that the daemon accepts it. These run the actual sandbox, so they catch a rejected
flag, a base reference the daemon will not pull, or a timeout that leaves a container
alive. Deselected by default (`-m "not docker"`); CI runs them in a dedicated job.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from exhibit_a.executor.base import ExecSpec, RepoState
from exhibit_a.executor.docker_exec import DockerExecutor, _base_reference

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "sandbox_smoke"

pytestmark = pytest.mark.docker


def _daemon_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return probe.returncode == 0


requires_daemon = pytest.mark.skipif(not _daemon_available(), reason="no Docker daemon")


def _repo() -> RepoState:
    return RepoState(path=str(FIXTURE), label="target", source="sandbox-smoke")


@requires_daemon
def test_the_base_image_resolves_to_a_real_digest():
    reference = _base_reference("docker")

    assert "@sha256:" in reference
    assert len(reference.split("@sha256:")[1]) == 64


@requires_daemon
def test_a_passing_test_runs_inside_the_pinned_image():
    outcome = DockerExecutor().run(
        _repo(),
        ExecSpec(
            test_path="test_smoke.py",
            test_code=(
                "from inventory import stock_for\n\n"
                "def test_missing_sku_is_zero():\n"
                "    assert stock_for({}, 'nope') == 0\n"
            ),
            command="python3 -m pytest -x -q test_smoke.py",
            timeout_s=300,
        ),
    )

    assert outcome.passed, outcome.log
    assert not outcome.timed_out


@requires_daemon
def test_a_failing_test_is_reported_as_a_failure_not_an_error():
    outcome = DockerExecutor().run(
        _repo(),
        ExecSpec(
            test_path="test_smoke.py",
            test_code=(
                "from inventory import stock_for\n\n"
                "def test_wrong_expectation():\n"
                "    assert stock_for({}, 'nope') == 1\n"
            ),
            command="python3 -m pytest -x -q test_smoke.py",
            timeout_s=300,
        ),
    )

    assert not outcome.passed
    assert "assert" in outcome.log.lower()


@requires_daemon
def test_a_timeout_leaves_no_container_running():
    """The whole point of naming containers: the budget must bind the daemon, not the client."""
    outcome = DockerExecutor().run(
        _repo(),
        ExecSpec(
            test_path="test_smoke.py",
            test_code="import time\n\n\ndef test_hangs():\n    time.sleep(600)\n",
            command="python3 -m pytest -x -q test_smoke.py",
            timeout_s=20,
        ),
    )

    assert outcome.timed_out
    assert outcome.exit_code == 124

    running = subprocess.run(
        ["docker", "ps", "--filter", "name=exhibit-a-run-", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert running.stdout.strip() == "", f"container survived its budget: {running.stdout}"


@requires_daemon
def test_source_is_never_mutated_by_the_run():
    before = (FIXTURE / "inventory.py").read_text()

    DockerExecutor().run(
        _repo(),
        ExecSpec(
            test_path="test_smoke.py",
            test_code=(
                "import pathlib\n\n"
                "def test_tries_to_write():\n"
                "    target = pathlib.Path('inventory.py')\n"
                "    try:\n"
                "        target.write_text('TAMPERED = True\\n')\n"
                "    except OSError:\n"
                "        pass\n"
            ),
            command="python3 -m pytest -x -q test_smoke.py",
            timeout_s=300,
        ),
    )

    assert (FIXTURE / "inventory.py").read_text() == before
    assert not (FIXTURE / "test_smoke.py").exists()
