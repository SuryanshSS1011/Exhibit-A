"""Safe, short-lived Git checkouts for two-revision Detective investigations."""

from __future__ import annotations

import logging
import re
import shutil
import socket
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit

from ..executor.base import RepoState

logger = logging.getLogger(__name__)

_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
_HOOKS_DISABLED = ["-c", "core.hooksPath=/dev/null"]
_GIT_TIMEOUT_S = 300


def checkout(repo_url: str, sha: str) -> RepoState:
    """Clone ``repo_url`` at ``sha`` into a new scratch directory.

    The returned state's parent directory owns the checkout and must be removed by
    the caller. Prefer :func:`checkout_context` or :func:`checkout_pair` so cleanup
    also happens when generation or execution raises.
    """
    validate_repo_url(repo_url)
    validate_sha(sha)

    logger.info("cloning %s at %s", repo_url, sha)
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
        resolved = _resolve_head(repo_path, sha)
    except Exception:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    return RepoState(path=str(repo_path), label="checkout", commit=resolved, source=repo_url)


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


@contextmanager
def checkout_triplet(
    repo_url: str, base_sha: str, fix_sha: str, control_sha: str
) -> Iterator[tuple[RepoState, RepoState, RepoState]]:
    """Yield buggy, fixed, and older/unrelated control states with cleanup."""
    with checkout_pair(repo_url, base_sha, fix_sha) as (buggy, fixed):
        with checkout_context(repo_url, control_sha, label="control") as control:
            yield buggy, fixed, control


def _resolve_head(repo_path: Path, requested: str) -> str:
    """Return the full 40-character SHA the checkout actually landed on.

    An abbreviation is convenient to type and is not a stable identifier: the same prefix
    can resolve differently as a repository grows. Evidence should name the commit, so the
    abbreviation is accepted at intake and resolved here, before it reaches a Case.
    """
    resolved = _git_output(["git", "-C", str(repo_path), *_HOOKS_DISABLED, "rev-parse", "HEAD"])
    if not _FULL_SHA_RE.fullmatch(resolved):
        raise ValueError(f"git resolved HEAD to something that is not a commit: {resolved!r}")
    if not resolved.startswith(requested.lower()):
        raise ValueError(
            f"checkout landed on {resolved} which does not match the requested {requested}"
        )
    return resolved


def validate_sha(sha: str) -> None:
    if not _SHA_RE.fullmatch(sha):
        raise ValueError("commit SHA must contain 7 to 40 hexadecimal characters")


def validate_repo_url(repo_url: str) -> None:
    parsed = urlsplit(repo_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("repo URL must be an HTTPS URL without embedded credentials")
    _require_public_host(parsed.hostname)


def _require_public_host(hostname: str) -> None:
    """Refuse a host that resolves into a private, loopback, or link-local range.

    Remote intake is reachable from the web API, so a repo URL is untrusted input that
    would otherwise aim ``git`` at localhost or a cloud metadata endpoint. A literal
    address is checked as given; a name is checked against every address it resolves
    to, so a name that resolves to a mix of public and private addresses fails closed.
    """
    host = hostname.strip("[]")
    addresses = [host] if _parse_address(host) is not None else _resolve(host)
    if not addresses:
        raise ValueError("repo URL host does not resolve to any address")
    for address in addresses:
        parsed = _parse_address(address)
        if parsed is None or not _is_public(parsed):
            raise ValueError("repo URL host must resolve to a public address")


def _resolve(hostname: str) -> list[str]:
    """Return every address ``hostname`` maps to. Seam so tests stay offline."""
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise ValueError(f"repo URL host does not resolve: {hostname!r}") from exc
    return [str(info[4][0]).split("%", 1)[0] for info in infos]


def _parse_address(value: str) -> IPv4Address | IPv6Address | None:
    try:
        return ip_address(value)
    except ValueError:
        return None


def _is_public(address: IPv4Address | IPv6Address) -> bool:
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _run_git(argv: list[str]) -> None:
    """Run one fixed-shape Git command without a shell."""
    subprocess.run(argv, check=True, capture_output=True, text=True, timeout=_GIT_TIMEOUT_S)


def _git_output(argv: list[str]) -> str:
    """Run one fixed-shape Git command and return its trimmed stdout."""
    result = subprocess.run(
        argv, check=True, capture_output=True, text=True, timeout=_GIT_TIMEOUT_S
    )
    return result.stdout.strip()
