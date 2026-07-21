"""Safe, short-lived Git checkouts for two-revision Detective investigations."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit

from ..executor.base import RepoState

_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_HOOKS_DISABLED = ["-c", "core.hooksPath=/dev/null"]


def checkout(repo_url: str, sha: str) -> RepoState:
    """Clone ``repo_url`` at ``sha`` into a new scratch directory.

    The returned state's parent directory owns the checkout and must be removed by
    the caller. Prefer :func:`checkout_context` or :func:`checkout_pair` so cleanup
    also happens when generation or execution raises.
    """
    _validate_repo_url(repo_url)
    _validate_sha(sha)

    scratch = Path(tempfile.mkdtemp(prefix="exhibit-a-git-"))
    repo_path = scratch / "repo"
    try:
        _run_git(
            [
                "git",
                *_HOOKS_DISABLED,
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                "--depth=1",
                "--no-tags",
                "--",
                repo_url,
                str(repo_path),
            ]
        )
        _run_git(
            [
                "git",
                "-C",
                str(repo_path),
                *_HOOKS_DISABLED,
                "fetch",
                "--filter=blob:none",
                "--depth=1",
                "--no-tags",
                "origin",
                sha,
            ]
        )
        _run_git(
            [
                "git",
                "-C",
                str(repo_path),
                *_HOOKS_DISABLED,
                "checkout",
                "--detach",
                sha,
            ]
        )
    except Exception:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    return RepoState(path=str(repo_path), label="checkout", commit=sha)


def cleanup(state: RepoState) -> None:
    """Remove a checkout returned by :func:`checkout`."""
    repo_path = Path(state.path).resolve()
    scratch = repo_path.parent
    if repo_path.name != "repo" or not scratch.name.startswith("exhibit-a-git-"):
        raise ValueError(f"refusing to clean a non-Exhibit-A checkout: {repo_path}")
    shutil.rmtree(scratch)


@contextmanager
def checkout_context(repo_url: str, sha: str, *, label: str) -> Iterator[RepoState]:
    state = replace(checkout(repo_url, sha), label=label)
    try:
        yield state
    finally:
        cleanup(state)


@contextmanager
def checkout_pair(
    repo_url: str, base_sha: str, fix_sha: str
) -> Iterator[tuple[RepoState, RepoState]]:
    """Yield ``(buggy, fixed)`` states and always remove both scratch trees."""
    with checkout_context(repo_url, base_sha, label="target") as buggy:
        with checkout_context(repo_url, fix_sha, label="base") as fixed:
            yield buggy, fixed


def _validate_sha(sha: str) -> None:
    if not _SHA_RE.fullmatch(sha):
        raise ValueError("commit SHA must contain 7 to 40 hexadecimal characters")


def _validate_repo_url(repo_url: str) -> None:
    parsed = urlsplit(repo_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("repo URL must be an HTTPS URL without embedded credentials")


def _run_git(argv: list[str]) -> None:
    """Run one fixed-shape Git command without a shell."""
    subprocess.run(argv, check=True, capture_output=True, text=True)
