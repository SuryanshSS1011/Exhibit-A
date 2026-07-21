"""Docker-backed executor with deterministic, per-repository environments.

Runs the test inside a container with the repo bind-mounted, source read-only and
the test file the only writable path, no network by default, and a hard timeout.
The default path requires a pinned repository lockfile and builds one cached image
per repository + lock content. It never guesses dependencies. Repositories without
a supported, pinned lockfile stay silent through ``EnvironmentSetupError``.

SECURITY: PR-supplied text never reaches a shell here. The command is passed as an
argv list, and the container runs as an unprivileged user with dropped caps.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
import tomllib

from .base import EnvironmentSetupError, ExecOutcome, ExecSpec, Executor, RepoState

DEFAULT_IMAGE = "exhibit-a-python-pytest:3.12"
_PINNED_REQUIREMENT = re.compile(r"^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[^\s;\\]+")


class DockerExecutor(Executor):
    """Isolate untrusted test runs in a short-lived container."""

    def __init__(self, base_image: str = DEFAULT_IMAGE, docker_bin: str = "docker"):
        self.base_image = base_image
        self.docker_bin = docker_bin

    def prepare(self, repo: RepoState) -> str | None:
        if self.base_image != DEFAULT_IMAGE:
            return self.base_image
        environment = _environment_spec(repo)
        inspect = subprocess.run(
            [self.docker_bin, "image", "inspect", environment.image],
            capture_output=True,
            text=True,
        )
        if inspect.returncode != 0:
            with tempfile.TemporaryDirectory(prefix="exhibit-a-env-") as tmp:
                context = Path(tmp)
                requirement_names = []
                for index, content in enumerate(environment.requirements):
                    name = f"requirements-{index}.txt"
                    (context / name).write_text(content)
                    requirement_names.append(name)
                (context / "Dockerfile").write_text(_dockerfile(requirement_names))
                build = subprocess.run(
                    [
                        self.docker_bin,
                        "build",
                        "--tag",
                        environment.image,
                        "--file",
                        str(context / "Dockerfile"),
                        str(context),
                    ],
                    capture_output=True,
                    text=True,
                )
                if build.returncode != 0:
                    detail = build.stderr.strip() or build.stdout.strip() or "no diagnostic"
                    raise EnvironmentSetupError(
                        f"pinned dependency image failed to build: {detail[-2000:]}"
                    )
        return environment.image

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
        work = workdir / "repo"
        try:
            shutil.copytree(src, work, ignore=shutil.ignore_patterns("__pycache__", ".git"))
            resolved_image = image or self.prepare(repo) or self.base_image
            docker_argv = [
                self.docker_bin,
                "run",
                "--rm",
                "--network",
                "none",
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
                "-v",
                f"{work}:/work:ro",
                "-w",
                "/work",
                resolved_image,
                *argv,
            ]
            start = time.monotonic()
            try:
                proc = subprocess.run(
                    docker_argv,
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


class _EnvironmentSpec:
    def __init__(self, image: str, requirements: tuple[str, ...]):
        self.image = image
        self.requirements = requirements


def _environment_spec(repo: RepoState) -> _EnvironmentSpec:
    root = Path(repo.path).resolve()
    if not root.is_dir():
        raise EnvironmentSetupError(f"repo checkout not found: {root}")

    poetry_lock = root / "poetry.lock"
    pipfile_lock = root / "Pipfile.lock"
    if poetry_lock.is_file():
        requirements = (_requirements_from_poetry(poetry_lock),)
    elif pipfile_lock.is_file():
        requirements = (_requirements_from_pipfile(pipfile_lock),)
    else:
        requirement_files = sorted(root.glob("requirements*.txt"))
        if not requirement_files:
            raise EnvironmentSetupError(
                "no poetry.lock, Pipfile.lock, or requirements*.txt was found; "
                "dependency discovery is intentionally disabled"
            )
        requirements = tuple(path.read_text() for path in requirement_files)
        for path, content in zip(requirement_files, requirements, strict=True):
            _validate_pinned_requirements(content, path.name)

    identity = repo.source or str(root)
    digest = hashlib.sha256(identity.encode())
    for content in requirements:
        digest.update(b"\0")
        digest.update(content.encode())
    return _EnvironmentSpec(f"exhibit-a-env:{digest.hexdigest()[:20]}", requirements)


def _requirements_from_poetry(path: Path) -> str:
    try:
        payload = tomllib.loads(path.read_text())
        packages = payload["package"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise EnvironmentSetupError(f"invalid poetry.lock: {exc}") from exc
    lines = []
    for package in packages:
        if package.get("optional", False) or package.get("category") == "dev":
            continue
        name = package.get("name")
        version = package.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise EnvironmentSetupError("poetry.lock contains an unpinned package")
        line = f"{name}=={version}"
        marker = package.get("marker")
        if isinstance(marker, str):
            line += f"; {marker}"
        lines.append(line)
    if not lines:
        raise EnvironmentSetupError("poetry.lock contains no installable main dependencies")
    return "\n".join(sorted(lines)) + "\n"


def _requirements_from_pipfile(path: Path) -> str:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise EnvironmentSetupError(f"invalid Pipfile.lock: {exc}") from exc
    lines = []
    for section in ("default", "develop"):
        packages = payload.get(section, {})
        if not isinstance(packages, dict):
            raise EnvironmentSetupError(f"Pipfile.lock section {section!r} is invalid")
        for name, metadata in packages.items():
            if not isinstance(metadata, dict):
                raise EnvironmentSetupError(f"Pipfile.lock package {name!r} is invalid")
            version = metadata.get("version")
            if not isinstance(version, str) or not version.startswith("=="):
                raise EnvironmentSetupError(f"Pipfile.lock package {name!r} is not pinned")
            line = f"{name}{version}"
            markers = metadata.get("markers")
            if isinstance(markers, str):
                line += f"; {markers}"
            lines.append(line)
    if not lines:
        raise EnvironmentSetupError("Pipfile.lock contains no pinned dependencies")
    return "\n".join(sorted(lines)) + "\n"


def _validate_pinned_requirements(content: str, name: str) -> None:
    for raw_line in content.splitlines():
        line = raw_line.strip().removesuffix("\\").strip()
        if not line or line.startswith("#") or line.startswith("--hash="):
            continue
        if line.startswith("-") or not _PINNED_REQUIREMENT.match(line):
            raise EnvironmentSetupError(
                f"{name} is not a self-contained pinned requirements file: {raw_line!r}"
            )


def _dockerfile(requirement_names: list[str]) -> str:
    copies = "\n".join(f"COPY {name} /tmp/locks/{name}" for name in requirement_names)
    installs = "\n".join(
        "RUN python -m pip install --disable-pip-version-check --no-cache-dir "
        f"--requirement /tmp/locks/{name}"
        for name in requirement_names
    )
    return (
        "FROM python:3.12-slim\n"
        "RUN python -m pip install --disable-pip-version-check --no-cache-dir pytest==8.4.1\n"
        f"{copies}\n{installs}\n"
        "USER 65534:65534\n"
    )
