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
import logging
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import tomllib
import uuid
from pathlib import Path

from ..replay_environment import PINNED_PYTEST_VERSION
from .base import (
    EnvironmentSetupError,
    ExecOutcome,
    ExecSpec,
    Executor,
    RepoState,
    SourceMutation,
    apply_source_mutation,
)

DEFAULT_IMAGE = "exhibit-a-python-pytest:3.12"
logger = logging.getLogger(__name__)

_BASE_IMAGE = "python:3.12-slim"
_CLEANUP_TIMEOUT_S = 30
_PULL_TIMEOUT_S = 600
_PINNED_REQUIREMENT = re.compile(r"^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[^\s;\\]+")


class DockerExecutor(Executor):
    """Isolate untrusted test runs in a short-lived container."""

    source_access = "disposable_copy"
    network_access = "disabled"
    isolation = "container"
    credential_access = "none"

    def __init__(self, base_image: str = DEFAULT_IMAGE, docker_bin: str = "docker"):
        self.base_image = base_image
        self.docker_bin = docker_bin

    def prepare(self, repo: RepoState) -> str | None:
        if self.base_image != DEFAULT_IMAGE:
            return self.base_image
        environment = _environment_spec(repo, base_reference=_base_reference(self.docker_bin))
        inspect = subprocess.run(
            [self.docker_bin, "image", "inspect", environment.image],
            capture_output=True,
            text=True,
        )
        if inspect.returncode != 0:
            logger.info(
                "building pinned environment %s from %s",
                environment.image,
                environment.base_reference,
            )
            with tempfile.TemporaryDirectory(prefix="exhibit-a-env-") as tmp:
                context = Path(tmp)
                requirement_names = []
                for index, content in enumerate(environment.requirements):
                    name = f"requirements-{index}.txt"
                    (context / name).write_text(content)
                    requirement_names.append(name)
                (context / "Dockerfile").write_text(
                    _dockerfile(
                        requirement_names,
                        environment.base_reference,
                        tuple(_carries_hashes(c) for c in environment.requirements),
                    )
                )
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

        # Copy the checkout to a scratch dir so the candidate test never mutates the
        # caller's source tree. We write the test on the host (not via a shell in the
        # container) so no model text is interpolated into a command line.
        workdir = Path(tempfile.mkdtemp(prefix="exhibit-a-"))
        work = workdir / "repo"
        try:
            shutil.copytree(src, work, ignore=shutil.ignore_patterns("__pycache__", ".git"))
            if mutation is not None:
                apply_source_mutation(work, mutation, test_path=spec.test_path)
            test_abs = work / spec.test_path
            test_abs.parent.mkdir(parents=True, exist_ok=True)
            test_abs.write_text(spec.test_code)

            image = spec.image or self.prepare(repo) or self.base_image
            container = f"exhibit-a-run-{uuid.uuid4().hex}"
            argv = [
                self.docker_bin,
                "run",
                "--rm",
                "--name",
                container,
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
                # Killing the client leaves the container running past its budget.
                logger.warning("run exceeded %ss; removing container %s", spec.timeout_s, container)
                _remove_container(self.docker_bin, container)
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
            container = f"exhibit-a-suite-{uuid.uuid4().hex}"
            docker_argv = [
                self.docker_bin,
                "run",
                "--rm",
                "--name",
                container,
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
                # Killing the client leaves the container running past its budget.
                logger.warning(
                    "existing suite exceeded %ss; removing container %s", timeout_s, container
                )
                _remove_container(self.docker_bin, container)
                return ExecOutcome(
                    exit_code=124,
                    stdout="",
                    stderr="TIMEOUT: existing suite exceeded wall-clock budget",
                    timed_out=True,
                    duration_s=time.monotonic() - start,
                )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


def _remove_container(docker_bin: str, name: str) -> None:
    """Stop the container itself; a killed client leaves it running on the daemon."""
    try:
        subprocess.run(
            [docker_bin, "rm", "--force", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_CLEANUP_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


class _EnvironmentSpec:
    def __init__(self, image: str, requirements: tuple[str, ...], base_reference: str):
        self.image = image
        self.requirements = requirements
        self.base_reference = base_reference


def _base_reference(docker_bin: str) -> str:
    """Resolve the base image to an immutable digest, pulling it once if absent.

    ``python:3.12-slim`` is a tag, and tags move. The environment cache key is content
    addressed over the repository and its lockfile only, so a moved tag used to change
    what a rebuilt image contains while the key stayed put -- evidence would name an
    environment that no longer existed, with nothing to signal the drift.
    """
    reference = _inspect_base_digest(docker_bin)
    if reference is None:
        pull = subprocess.run(
            [docker_bin, "pull", "--quiet", _BASE_IMAGE],
            capture_output=True,
            text=True,
            timeout=_PULL_TIMEOUT_S,
        )
        if pull.returncode != 0:
            detail = pull.stderr.strip() or pull.stdout.strip() or "no diagnostic"
            raise EnvironmentSetupError(f"base image {_BASE_IMAGE} could not be pulled: {detail}")
        reference = _inspect_base_digest(docker_bin)
    if reference is None:
        raise EnvironmentSetupError(f"base image {_BASE_IMAGE} exposes no digest to pin to")
    return reference


def _inspect_base_digest(docker_bin: str) -> str | None:
    inspect = subprocess.run(
        [docker_bin, "image", "inspect", "--format", "{{index .RepoDigests 0}}", _BASE_IMAGE],
        capture_output=True,
        text=True,
        timeout=_CLEANUP_TIMEOUT_S,
    )
    reference = inspect.stdout.strip()
    if inspect.returncode != 0 or "@sha256:" not in reference:
        return None
    return reference


def _environment_spec(repo: RepoState, *, base_reference: str) -> _EnvironmentSpec:
    root = Path(repo.path).resolve()
    if not root.is_dir():
        raise EnvironmentSetupError(f"repo checkout not found: {root}")

    uv_lock = root / "uv.lock"
    poetry_lock = root / "poetry.lock"
    pipfile_lock = root / "Pipfile.lock"
    # uv.lock is tried first: it is a complete resolution and carries artifact hashes, so
    # it pins bytes rather than versions.
    if uv_lock.is_file():
        requirements = (_requirements_from_uv(uv_lock),)
    elif poetry_lock.is_file():
        requirements = (_requirements_from_poetry(poetry_lock),)
    elif pipfile_lock.is_file():
        requirements = (_requirements_from_pipfile(pipfile_lock),)
    else:
        requirement_files = sorted(root.glob("requirements*.txt"))
        if not requirement_files:
            raise EnvironmentSetupError(
                "no uv.lock, poetry.lock, Pipfile.lock, or requirements*.txt was found; "
                "dependency discovery is intentionally disabled"
            )
        requirements = tuple(path.read_text() for path in requirement_files)
        for path, content in zip(requirement_files, requirements, strict=True):
            _validate_pinned_requirements(content, path.name)

    identity = repo.source or str(root)
    digest = hashlib.sha256(identity.encode())
    # The base image and pinned pytest are part of what the image *is*, so they belong in
    # the key that decides whether a cached image may be reused.
    for component in (base_reference, PINNED_PYTEST_VERSION):
        digest.update(b"\0")
        digest.update(component.encode())
    for content in requirements:
        digest.update(b"\0")
        digest.update(content.encode())
    return _EnvironmentSpec(
        f"exhibit-a-env:{digest.hexdigest()[:20]}", requirements, base_reference
    )


def _requirements_from_uv(path: Path) -> str:
    """Convert a uv lockfile into an exactly pinned, hash-bearing requirements file.

    A uv lockfile is a complete resolution: every transitive dependency is present with an
    exact version and the sha256 of every artifact it may install. That is strictly more
    than a version pin, so the generated file carries the hashes through and the build
    installs under ``--require-hashes``.
    """
    try:
        payload = tomllib.loads(path.read_text())
        packages = payload["package"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise EnvironmentSetupError(f"invalid uv.lock: {exc}") from exc
    if not isinstance(packages, list):
        raise EnvironmentSetupError("uv.lock package table is invalid")

    entries: list[str] = []
    for package in packages:
        if not isinstance(package, dict):
            raise EnvironmentSetupError("uv.lock package entry is invalid")
        source = package.get("source")
        if not isinstance(source, dict) or not source:
            raise EnvironmentSetupError("uv.lock package declares no source")
        kind = next(iter(source))
        if kind in {"editable", "virtual", "directory"}:
            # The project under test itself; its source is the checkout, not an index.
            continue
        if kind != "registry":
            raise EnvironmentSetupError(
                f"uv.lock package {package.get('name')!r} comes from {kind!r}, "
                "which cannot be reproduced from an index"
            )
        name = package.get("name")
        version = package.get("version")
        if not isinstance(name, str) or not isinstance(version, str) or not version:
            raise EnvironmentSetupError("uv.lock contains an unpinned package")
        hashes = _uv_artifact_hashes(package)
        if not hashes:
            raise EnvironmentSetupError(f"uv.lock package {name!r} carries no artifact hash")
        requirement = f"{name}=={version}"
        marker = package.get("marker")
        if isinstance(marker, str) and marker.strip():
            requirement += f" ; {marker.strip()}"
        entries.append(" \\\n    ".join([requirement, *(f"--hash={digest}" for digest in hashes)]))

    if not entries:
        raise EnvironmentSetupError("uv.lock contains no installable dependencies")
    return "\n".join(sorted(entries)) + "\n"


def _uv_artifact_hashes(package: dict) -> tuple[str, ...]:
    """Every sha256 pip may need, since which artifact it picks depends on the platform."""
    digests: list[str] = []
    sdist = package.get("sdist")
    if isinstance(sdist, dict) and isinstance(sdist.get("hash"), str):
        digests.append(sdist["hash"])
    wheels = package.get("wheels")
    if isinstance(wheels, list):
        for wheel in wheels:
            if isinstance(wheel, dict) and isinstance(wheel.get("hash"), str):
                digests.append(wheel["hash"])
    return tuple(sorted({d for d in digests if d.startswith("sha256:")}))


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


def _carries_hashes(content: str) -> bool:
    """True when a lockfile ships artifact hashes, so pip can be told to demand them."""
    return "--hash=" in content


def _dockerfile(
    requirement_names: list[str],
    base_reference: str,
    hashed: tuple[bool, ...] | None = None,
) -> str:
    flags = hashed or (False,) * len(requirement_names)
    copies = "\n".join(f"COPY {name} /tmp/locks/{name}" for name in requirement_names)
    installs = "\n".join(
        "RUN python -m pip install --disable-pip-version-check --no-cache-dir "
        # A lockfile that went to the trouble of pinning hashes should have them
        # enforced, not silently ignored. pip demands all-or-nothing, so a file with
        # partial hashes fails the build rather than installing the unhashed remainder.
        + ("--require-hashes " if carries else "")
        + f"--requirement /tmp/locks/{name}"
        for name, carries in zip(requirement_names, flags, strict=True)
    )
    return (
        f"FROM {base_reference}\n"
        "RUN python -m pip install --disable-pip-version-check --no-cache-dir "
        f"pytest=={PINNED_PYTEST_VERSION}\n"
        f"{copies}\n{installs}\n"
        "USER 65534:65534\n"
    )
