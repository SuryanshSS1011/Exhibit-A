"""Local subprocess executor — a no-Docker fallback for fast dev/testing.

NOT isolated: it runs the test in a subprocess on the host. Use ONLY against
trusted fixtures during development. The Docker executor is the real one; this
exists so the engine and verdict layer can be exercised without container
overhead, and so CI can run without a Docker daemon.

It copies the checkout to a scratch dir before writing the candidate test, so it
never mutates the caller's source tree (which would pollute fixtures and let stale
test files leak into later runs).
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from .base import (
    ExecOutcome,
    ExecSpec,
    Executor,
    RepoState,
    SourceMutation,
    apply_source_mutation,
)

logger = logging.getLogger(__name__)

_KILL_TIMEOUT_S = 10


class LocalExecutor(Executor):
    """Run the test file in a host subprocess against a disposable copy."""

    source_access = "disposable_copy"
    network_access = "host_unrestricted"
    isolation = "host_subprocess"
    credential_access = "ambient_host"

    def prepare(self, repo: RepoState) -> str | None:
        return None

    def run(self, repo: RepoState, spec: ExecSpec) -> ExecOutcome:
        return self._run_in_copy(repo, spec)

    def run_mutant(self, repo: RepoState, spec: ExecSpec, mutation: SourceMutation) -> ExecOutcome:
        return self._run_in_copy(repo, spec, mutation)

    def _run_in_copy(
        self,
        repo: RepoState,
        spec: ExecSpec,
        mutation: SourceMutation | None = None,
    ) -> ExecOutcome:
        src = Path(repo.path).resolve()
        if not src.is_dir():
            raise FileNotFoundError(f"repo checkout not found: {src}")

        workdir = Path(tempfile.mkdtemp(prefix="exhibit-a-"))
        try:
            # Copy the checkout so the candidate test never touches the source tree.
            work = workdir / "repo"
            shutil.copytree(src, work, ignore=shutil.ignore_patterns("__pycache__", ".git"))
            if mutation is not None:
                apply_source_mutation(work, mutation, test_path=spec.test_path)

            test_abs = work / spec.test_path
            test_abs.parent.mkdir(parents=True, exist_ok=True)
            test_abs.write_text(spec.test_code)

            return _run_capped(
                shlex.split(spec.command),
                cwd=work,
                timeout_s=spec.timeout_s,
                timeout_message="TIMEOUT: exceeded per-run wall-clock budget",
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def run_suite(
        self,
        repo: RepoState,
        argv: list[str],
        *,
        image: str | None = None,
        timeout_s: int = 120,
    ) -> ExecOutcome:
        src = Path(repo.path).resolve()
        workdir = Path(tempfile.mkdtemp(prefix="exhibit-a-suite-"))
        try:
            work = workdir / "repo"
            shutil.copytree(src, work, ignore=shutil.ignore_patterns("__pycache__", ".git"))
            return _run_capped(
                argv,
                cwd=work,
                timeout_s=timeout_s,
                timeout_message="TIMEOUT: existing suite exceeded wall-clock budget",
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


def _run_capped(
    argv: list[str],
    *,
    cwd: Path,
    timeout_s: int,
    timeout_message: str,
) -> ExecOutcome:
    """Run one command in its own process group so a timeout stops the whole tree.

    ``subprocess.run(timeout=...)`` signals only the direct child, so a runner that
    spawned workers leaves them running past the budget. The candidate test is
    untrusted, so the budget has to bind everything it started, not just pytest.
    """
    start = time.monotonic()
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        logger.warning("run exceeded %ss on the host; killing its process group", timeout_s)
        _kill_process_group(process)
        return ExecOutcome(
            exit_code=124,
            stdout="",
            stderr=timeout_message,
            timed_out=True,
            duration_s=time.monotonic() - start,
        )
    return ExecOutcome(
        exit_code=process.returncode,
        stdout=stdout,
        stderr=stderr,
        duration_s=time.monotonic() - start,
    )


def _kill_process_group(process: subprocess.Popen[str]) -> None:
    """Kill the group; killing the child alone orphans whatever it spawned."""
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except OSError:
        process.kill()
    try:
        process.communicate(timeout=_KILL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        pass
