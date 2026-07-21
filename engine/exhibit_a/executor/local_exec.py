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

import shlex
import shutil
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


class LocalExecutor(Executor):
    """Run the test file in a host subprocess against a disposable copy."""

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

            start = time.monotonic()
            try:
                proc = subprocess.run(
                    shlex.split(spec.command),
                    cwd=work,
                    capture_output=True,
                    text=True,
                    timeout=spec.timeout_s,
                )
                return ExecOutcome(
                    exit_code=proc.returncode,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    duration_s=time.monotonic() - start,
                )
            except subprocess.TimeoutExpired:
                return ExecOutcome(
                    exit_code=124,
                    stdout="",
                    stderr="TIMEOUT: exceeded per-run wall-clock budget",
                    timed_out=True,
                    duration_s=time.monotonic() - start,
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
            start = time.monotonic()
            try:
                proc = subprocess.run(
                    argv,
                    cwd=work,
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                )
                return ExecOutcome(
                    exit_code=proc.returncode,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    duration_s=time.monotonic() - start,
                )
            except subprocess.TimeoutExpired:
                return ExecOutcome(
                    exit_code=124,
                    stdout="",
                    stderr="TIMEOUT: existing suite exceeded wall-clock budget",
                    timed_out=True,
                    duration_s=time.monotonic() - start,
                )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
