"""Safe causal search for a deterministic Detective reproduction."""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .git_checkout import validate_repo_url, validate_sha

_HOOKS_DISABLED = ["-c", "core.hooksPath=/dev/null"]
_CULPRIT_RE = re.compile(r"^([0-9a-fA-F]{40}) is the first bad commit$", re.MULTILINE)


@dataclass(frozen=True)
class BisectResult:
    culprit: str
    parent: str
    log: str


def bisect_reproduction(
    repo_url: str,
    *,
    bad_sha: str,
    good_sha: str,
    test_path: str,
    test_code: str,
    run_command: str,
    image: str,
    docker_bin: str = "docker",
    timeout_s: int = 900,
) -> BisectResult:
    """Run a generated reproduction over history, then return its causal boundary.

    Bisect is only a locator. The caller must re-run the ordinary flip check against
    ``culprit`` and ``parent`` before upgrading evidence.
    """
    validate_repo_url(repo_url)
    validate_sha(bad_sha)
    validate_sha(good_sha)
    test_rel = _validate_test_path(test_path)
    test_argv = _validate_run_command(run_command, test_path)

    scratch = Path(tempfile.mkdtemp(prefix="exhibit-a-bisect-"))
    repo = scratch / "repo"
    try:
        _run_git(
            [
                "git",
                *_HOOKS_DISABLED,
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                "--no-tags",
                "--",
                repo_url,
                str(repo),
            ]
        )
        _run_git(
            [
                "git",
                "-C",
                str(repo),
                *_HOOKS_DISABLED,
                "fetch",
                "--filter=blob:none",
                "--no-tags",
                "origin",
                bad_sha,
                good_sha,
            ]
        )
        _run_git(["git", "-C", str(repo), *_HOOKS_DISABLED, "checkout", "--detach", bad_sha])
        test_abs = repo.joinpath(*test_rel.parts)
        test_abs.parent.mkdir(parents=True, exist_ok=True)
        test_abs.write_text(test_code)
        _run_git(["git", "-C", str(repo), *_HOOKS_DISABLED, "bisect", "start", bad_sha, good_sha])
        docker_argv = [
            docker_bin,
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
            f"{repo}:/work:ro",
            "-w",
            "/work",
            image,
            *test_argv,
        ]
        proc = _run_git(
            [
                "git",
                "-C",
                str(repo),
                *_HOOKS_DISABLED,
                "bisect",
                "run",
                *docker_argv,
            ],
            timeout=timeout_s,
        )
        log = "\n".join(part for part in (proc.stdout, proc.stderr) if part).strip()
        match = _CULPRIT_RE.search(log)
        if match is None:
            raise RuntimeError("git bisect did not identify a first bad commit")
        culprit = match.group(1)
        validate_sha(culprit)
        parent_proc = _run_git(
            [
                "git",
                "-C",
                str(repo),
                *_HOOKS_DISABLED,
                "rev-parse",
                f"{culprit}^",
            ]
        )
        parent = parent_proc.stdout.strip()
        validate_sha(parent)
        return BisectResult(culprit=culprit, parent=parent, log=log)
    finally:
        if repo.is_dir():
            subprocess.run(
                ["git", "-C", str(repo), *_HOOKS_DISABLED, "bisect", "reset"],
                capture_output=True,
                text=True,
            )
        shutil.rmtree(scratch, ignore_errors=True)


def _validate_test_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or path.suffix != ".py"
        or not path.name.startswith("test_")
    ):
        raise ValueError("bisect test path must be a repository-relative pytest file")
    return path


def _validate_run_command(command: str, test_path: str) -> list[str]:
    if any(marker in command for marker in (";", "&", "|", ">", "<", "`", "$")):
        raise ValueError("bisect run command contains a shell control character")
    argv = shlex.split(command)
    if len(argv) >= 3 and argv[1:3] == ["-m", "pytest"]:
        pytest_args = argv[3:]
    elif argv and PurePosixPath(argv[0]).name in {"pytest", "pytest3"}:
        pytest_args = argv[1:]
    else:
        raise ValueError("bisect run command must invoke pytest directly")
    allowed_flags = {"-x", "-q", "--tb=short", "--disable-warnings"}
    positional = [arg for arg in pytest_args if not arg.startswith("-")]
    flags = [arg for arg in pytest_args if arg.startswith("-")]
    if positional != [test_path] or any(flag not in allowed_flags for flag in flags):
        raise ValueError("bisect run command must target only the generated test")
    return argv


def _run_git(argv: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
