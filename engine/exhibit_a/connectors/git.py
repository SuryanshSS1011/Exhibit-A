"""Local-only Git object metadata connector."""

from __future__ import annotations

import hashlib
import os
import re
import resource
import shutil
import signal
import stat
import subprocess
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..executor.base import RepoState
from ..intake.git_checkout import validate_sha
from .base import (
    ConnectorDescriptor,
    ConnectorOutput,
    ConnectorSecurity,
    EvidenceKind,
    EvidenceProvenance,
    Freshness,
    credential_free_source,
    hash_payload,
)

_GIT_PREFIX = (
    "--no-pager",
    "--no-replace-objects",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.pager=cat",
    "-c",
    "color.ui=false",
)
_HEX = frozenset("0123456789abcdef")
_UNSAFE_CONFIG = re.compile(r"(?im)^\s*(?:promisor|partialclone)\s*=|^\s*\[include(?:if)?\b")
_MAX_PARENTS = 64
_MAX_CHANGES = 10_000
_MAX_PATH_BYTES = 4096
_MAX_OBJECT_STORE_ENTRIES = 100_000


@dataclass(frozen=True)
class GitMetadataRequest:
    repo: RepoState


@dataclass(frozen=True)
class GitChange:
    status: str
    old_mode: str
    new_mode: str
    old_blob: str
    new_blob: str
    path: str


@dataclass(frozen=True)
class GitMetadata:
    commit: str
    tree: str
    parents: tuple[str, ...]
    author_time: str
    committer_time: str
    message_sha256: str
    signature_present: bool
    diff_base: str | None
    changes: tuple[GitChange, ...]

    def payload(self) -> dict[str, object]:
        return asdict(self)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class GitMetadataConnector:
    """Read immutable commit metadata without fetching or invoking repository helpers."""

    descriptor = ConnectorDescriptor(
        id="git_metadata",
        version="1",
        capabilities=(EvidenceKind.GIT_METADATA,),
        freshness_basis=Freshness.IMMUTABLE_REVISION,
        security=ConnectorSecurity(
            source_access="read_only",
            network_access="host_unrestricted",
            isolation="host_subprocess",
            credential_access="ambient_host",
        ),
    )

    def __init__(
        self,
        *,
        git_bin: str = "git",
        timeout_s: float = 5.0,
        max_output_bytes: int = 1_048_576,
        clock: Callable[[], datetime] = _utc_now,
    ):
        resolved_git = shutil.which(git_bin)
        if resolved_git is None or timeout_s <= 0 or max_output_bytes < 1024:
            raise ValueError("Git connector limits and binary must be valid")
        self._git_bin = resolved_git
        self._timeout_s = timeout_s
        self._max_output_bytes = max_output_bytes
        self._clock = clock

    def collect(self, request: GitMetadataRequest) -> ConnectorOutput[GitMetadata]:
        root, git_dir = _validate_checkout(request.repo)
        revision = request.repo.commit
        if revision is None:
            raise ValueError("Git metadata connector requires an explicit commit SHA")
        validate_sha(revision)
        _reject_network_capable_object_store(git_dir)
        _reject_symlinked_object_store(git_dir)
        observed_at = self._clock()
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("connector clock must return a timezone-aware datetime")

        resolved = self._run(
            root,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{revision}^{{commit}}",
            output_limit=256,
        )
        commit = resolved.decode("ascii").strip().lower()
        _validate_object_id(commit, "resolved commit")
        size_raw = self._run(root, "cat-file", "-s", commit, output_limit=64)
        try:
            commit_size = int(size_raw.strip())
        except ValueError as exc:
            raise ValueError("Git commit object size was invalid") from exc
        if commit_size < 1 or commit_size > min(65_536, self._max_output_bytes):
            raise ValueError("Git commit object exceeded the configured limit")
        raw_commit = self._run(root, "cat-file", "commit", commit, output_limit=commit_size)
        header = _parse_commit(commit, raw_commit)
        diff_base = header.parents[0] if header.parents else None
        raw_diff = self._diff(root, commit, diff_base)
        metadata = GitMetadata(
            commit=commit,
            tree=header.tree,
            parents=header.parents,
            author_time=header.author_time,
            committer_time=header.committer_time,
            message_sha256=header.message_sha256,
            signature_present=header.signature_present,
            diff_base=diff_base,
            changes=_parse_changes(raw_diff),
        )
        source = credential_free_source(request.repo.source)
        request_sha256 = hash_payload(
            {"requested_revision": revision, "source": source, "state": request.repo.label}
        )
        response_sha256 = hash_payload(metadata.payload())
        artifact_sha256 = hashlib.sha256(
            raw_commit + b"\0exhibit-a-git-diff\0" + raw_diff
        ).hexdigest()
        content_sha256 = hash_payload(
            {"request_sha256": request_sha256, "response_sha256": response_sha256}
        )
        provenance = EvidenceProvenance(
            evidence_id=uuid.uuid4().hex,
            connector_id=self.descriptor.id,
            connector_version=self.descriptor.version,
            capability=EvidenceKind.GIT_METADATA,
            source=source,
            source_revision=commit,
            observed_at=observed_at.astimezone(timezone.utc).isoformat(),
            source_updated_at=metadata.committer_time,
            freshness=self.descriptor.freshness_basis,
            description="Read immutable commit, tree, parent, timestamp, and changed-path metadata",
            request_sha256=request_sha256,
            response_sha256=response_sha256,
            artifact_sha256=artifact_sha256,
            content_sha256=content_sha256,
            security=self.descriptor.security,
        )
        return ConnectorOutput(payload=metadata, provenance=provenance)

    def _diff(self, root: Path, commit: str, parent: str | None) -> bytes:
        args = [
            "diff-tree",
            "--no-commit-id",
            "--raw",
            "-z",
            "--no-abbrev",
            "--no-renames",
            "--no-ext-diff",
        ]
        if parent is None:
            args.extend(("--root", commit))
        else:
            args.extend((parent, commit))
        args.append("--")
        return self._run(root, *args)

    def _run(self, root: Path, *args: str, output_limit: int | None = None) -> bytes:
        argv = [self._git_bin, *_GIT_PREFIX, *args]
        env = {
            "GCM_INTERACTIVE": "never",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
            "PATH": os.defpath,
        }
        with tempfile.TemporaryFile() as output:
            limit = min(output_limit or self._max_output_bytes, self._max_output_bytes)

            def limit_output_file() -> None:
                resource.setrlimit(resource.RLIMIT_FSIZE, (limit + 1, limit + 1))

            try:
                process = subprocess.Popen(
                    argv,
                    cwd=root,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=output,
                    start_new_session=True,
                    preexec_fn=limit_output_file,
                )
                returncode = process.wait(timeout=self._timeout_s)
            except subprocess.TimeoutExpired as exc:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise RuntimeError("Git metadata collection timed out") from exc
            size = output.tell()
            if size > limit:
                raise RuntimeError("Git metadata output exceeded the configured limit")
            output.seek(0)
            content = output.read()
        if returncode != 0:
            raise RuntimeError("Git metadata command failed")
        return content


@dataclass(frozen=True)
class _CommitHeader:
    tree: str
    parents: tuple[str, ...]
    author_time: str
    committer_time: str
    message_sha256: str
    signature_present: bool


def _validate_checkout(repo: RepoState) -> tuple[Path, Path]:
    root = Path(repo.path).resolve()
    git_dir = root / ".git"
    if not root.is_dir() or not git_dir.is_dir() or git_dir.is_symlink():
        raise ValueError("Git metadata connector requires a standard local Git checkout")
    return root, git_dir


def _reject_network_capable_object_store(git_dir: Path) -> None:
    for name in ("config", "config.worktree"):
        config_path = git_dir / name
        if not config_path.exists():
            continue
        config = _read_bounded_regular_file(config_path, 65_536).decode("utf-8")
        if _UNSAFE_CONFIG.search(config):
            raise ValueError("partial/promisor or included Git config is not supported")
    info = git_dir / "objects" / "info"
    if (info / "alternates").exists() or (info / "http-alternates").exists():
        raise ValueError("Git object alternates are not supported")


def _reject_symlinked_object_store(git_dir: Path) -> None:
    objects = git_dir / "objects"
    if not objects.is_dir() or objects.is_symlink():
        raise ValueError("Git objects directory must be local and non-symlinked")
    entries = 0
    for root, directories, files in os.walk(objects, followlinks=False):
        for name in (*directories, *files):
            entries += 1
            if entries > _MAX_OBJECT_STORE_ENTRIES:
                raise ValueError("Git object store exceeded the validation limit")
            if (Path(root) / name).is_symlink():
                raise ValueError("Git object store must not contain symlinks")


def _read_bounded_regular_file(path: Path, limit: int) -> bytes:
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("Git repository config must be a regular local file") from exc
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError("Git repository config must be a regular local file")
        if file_stat.st_size > limit:
            raise ValueError("Git repository config exceeded the configured limit")
        content = os.read(descriptor, limit + 1)
        if len(content) > limit:
            raise ValueError("Git repository config exceeded the configured limit")
        return content
    finally:
        os.close(descriptor)


def _parse_commit(commit: str, raw: bytes) -> _CommitHeader:
    headers, separator, message = raw.partition(b"\n\n")
    if not separator:
        raise ValueError("Git commit object had no header boundary")
    tree: str | None = None
    parents: list[str] = []
    author_time: str | None = None
    committer_time: str | None = None
    signature_present = False
    for line in headers.splitlines():
        if line.startswith((b" ", b"\t")):
            continue
        key, space, value = line.partition(b" ")
        if not space:
            raise ValueError("Git commit header was malformed")
        if key == b"tree":
            tree = value.decode("ascii").lower()
        elif key == b"parent":
            parents.append(value.decode("ascii").lower())
        elif key == b"author":
            author_time = _identity_time(value)
        elif key == b"committer":
            committer_time = _identity_time(value)
        elif key == b"gpgsig":
            signature_present = True
    if tree is None or author_time is None or committer_time is None:
        raise ValueError("Git commit object lacked required metadata")
    _validate_object_id(commit, "commit")
    _validate_object_id(tree, "tree")
    if len(parents) > _MAX_PARENTS:
        raise ValueError("Git commit has too many parents")
    for parent in parents:
        _validate_object_id(parent, "parent")
    return _CommitHeader(
        tree,
        tuple(parents),
        author_time,
        committer_time,
        hashlib.sha256(message).hexdigest(),
        signature_present,
    )


def _identity_time(value: bytes) -> str:
    try:
        timestamp, offset = value.rsplit(b" ", 2)[-2:]
        epoch = int(timestamp)
        if not re.fullmatch(rb"[+-]\d{4}", offset):
            raise ValueError
        return datetime.fromtimestamp(epoch, timezone.utc).isoformat()
    except (OverflowError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Git identity timestamp was invalid") from exc


def _parse_changes(raw: bytes) -> tuple[GitChange, ...]:
    if not raw:
        return ()
    fields = raw.split(b"\0")
    if fields[-1] == b"":
        fields.pop()
    if len(fields) % 2 or len(fields) // 2 > _MAX_CHANGES:
        raise ValueError("Git changed-path metadata had an unexpected size or shape")
    changes: list[GitChange] = []
    for index in range(0, len(fields), 2):
        header = fields[index].split()
        if len(header) != 5 or not header[0].startswith(b":"):
            raise ValueError("Git changed-path header had an unexpected shape")
        status = header[4].decode("ascii")
        if len(status) != 1 or status not in "ACDMTUXB":
            raise ValueError("Git changed-path status was unsupported")
        old_mode = header[0][1:].decode("ascii")
        new_mode = header[1].decode("ascii")
        if not re.fullmatch(r"[0-7]{6}", old_mode) or not re.fullmatch(r"[0-7]{6}", new_mode):
            raise ValueError("Git changed-path mode was invalid")
        old_blob = header[2].decode("ascii").lower()
        new_blob = header[3].decode("ascii").lower()
        _validate_object_id(old_blob, "old blob", allow_zero=True)
        _validate_object_id(new_blob, "new blob", allow_zero=True)
        raw_path = fields[index + 1]
        if len(raw_path) > _MAX_PATH_BYTES:
            raise ValueError("Git changed path exceeded the configured limit")
        try:
            path = raw_path.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Git changed path was not valid UTF-8") from exc
        changes.append(GitChange(status, old_mode, new_mode, old_blob, new_blob, path))
    return tuple(changes)


def _validate_object_id(value: str, label: str, *, allow_zero: bool = False) -> None:
    if len(value) != 40 or any(character not in _HEX for character in value):
        raise ValueError(f"Git {label} was not a full SHA-1 object ID")
    if not allow_zero and set(value) == {"0"}:
        raise ValueError(f"Git {label} was a null object ID")
