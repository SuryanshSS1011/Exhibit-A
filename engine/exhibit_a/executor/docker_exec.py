"""Docker-backed executor — the hackathon default (plan §2, Phase 0).

Runs the test inside a container with the repo bind-mounted, source read-only and
the test file the only writable path, no network by default, and a hard timeout.
This is intentionally minimal: environment-setup automation (pip/poetry inference)
is a v1 concern (§2 "the single hardest problem is environment setup"). For the
MVP we assume the caller (or a per-repo cached image) already has deps installed,
or that the repo needs only stdlib + pytest.

SECURITY: PR-supplied text never reaches a shell here. The command is passed as an
argv list, and the container runs as an unprivileged user with dropped caps.
"""

from __future__ import annotations

import shutil
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

from .base import ExecOutcome, ExecSpec, Executor, RepoState

DEFAULT_IMAGE = "exhibit-a-python-pytest:3.12"
_RUNNER_DOCKERFILE = """FROM python:3.12-slim
RUN pip install --no-cache-dir pytest==8.4.1
USER 65534:65534
"""


class DockerExecutor(Executor):
    """Isolate untrusted test runs in a short-lived container."""

    def __init__(self, base_image: str = DEFAULT_IMAGE, docker_bin: str = "docker"):
        self.base_image = base_image
        self.docker_bin = docker_bin

    def prepare(self, repo: RepoState) -> str | None:
        if self.base_image != DEFAULT_IMAGE:
            return self.base_image
        inspect = subprocess.run(
            [self.docker_bin, "image", "inspect", self.base_image],
            capture_output=True,
            text=True,
        )
        if inspect.returncode != 0:
            build = subprocess.run(
                [self.docker_bin, "build", "-t", self.base_image, "-"],
                input=_RUNNER_DOCKERFILE,
                capture_output=True,
                text=True,
            )
            if build.returncode != 0:
                detail = build.stderr.strip() or build.stdout.strip()
                raise RuntimeError(f"could not build pytest runner image: {detail[-2000:]}")
        return self.base_image

    def run(self, repo: RepoState, spec: ExecSpec) -> ExecOutcome:
        src = Path(repo.path).resolve()
        if not src.is_dir():
            raise FileNotFoundError(f"repo checkout not found: {src}")

        # Copy the checkout to a scratch dir so the candidate test never mutates the
        # caller's source tree. We write the test on the host (not via a shell in the
        # container) so no model text is interpolated into a command line.
        workdir = Path(tempfile.mkdtemp(prefix="exhibit-a-"))
        work = workdir / "repo"
        try:
            shutil.copytree(src, work, ignore=shutil.ignore_patterns("__pycache__", ".git"))
            test_abs = work / spec.test_path
            test_abs.parent.mkdir(parents=True, exist_ok=True)
            test_abs.write_text(spec.test_code)

            image = spec.image or self.prepare(repo) or self.base_image
            argv = [
                self.docker_bin,
                "run",
                "--rm",
                "--network",
                "none" if not spec.network else "bridge",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=128m",
                "--pids-limit",
                "512",
                "--memory",
                "2g",
                "--cpus",
                "2",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                "--env",
                "PYTHONPYCACHEPREFIX=/tmp/pycache",
                "-v",
                f"{work}:/work:ro",
                "-w",
                "/work",
                image,
            ]
            argv.extend(shlex.split(spec.command))

            start = time.monotonic()
            try:
                proc = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=spec.timeout_s,
                )
                return ExecOutcome(
                    exit_code=proc.returncode,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    timed_out=False,
                    duration_s=time.monotonic() - start,
                )
            except subprocess.TimeoutExpired as e:
                return ExecOutcome(
                    exit_code=124,
                    stdout=e.stdout or "" if isinstance(e.stdout, str) else "",
                    stderr="TIMEOUT: exceeded per-run wall-clock budget",
                    timed_out=True,
                    duration_s=time.monotonic() - start,
                )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
