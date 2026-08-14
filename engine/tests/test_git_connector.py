from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from exhibit_a.connectors import (
    EvidenceKind,
    Freshness,
    GitMetadataConnector,
    GitMetadataRequest,
    hash_payload,
)
from exhibit_a.executor.base import RepoState


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Test Author",
            "GIT_AUTHOR_EMAIL": "author@example.com",
            "GIT_COMMITTER_NAME": "Test Committer",
            "GIT_COMMITTER_EMAIL": "committer@example.com",
        },
    )
    return completed.stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "odd\tname.txt").write_text("evidence\n")
    _git(root, "add", "--", "odd\tname.txt")
    _git(root, "commit", "-q", "-m", "initial evidence")
    return root, _git(root, "rev-parse", "HEAD")


def test_git_connector_collects_immutable_non_pii_metadata(tmp_path: Path):
    root, commit = _repo(tmp_path)
    observed_at = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
    connector = GitMetadataConnector(clock=lambda: observed_at)
    request = GitMetadataRequest(
        RepoState(
            str(root),
            "target",
            commit=commit[:12],
            source="https://user:secret@example.com/org/repo.git?token=hidden",
        )
    )

    evidence = connector.collect(request)

    assert evidence.payload.commit == commit
    assert len(evidence.payload.tree) == 40
    assert evidence.payload.parents == ()
    assert [(change.status, change.path) for change in evidence.payload.changes] == [
        ("A", "odd\tname.txt")
    ]
    assert evidence.provenance.capability is EvidenceKind.GIT_METADATA
    assert evidence.provenance.freshness is Freshness.IMMUTABLE_REVISION
    assert evidence.provenance.source == "https://example.com/org/repo.git"
    assert evidence.provenance.source_revision == commit
    assert evidence.provenance.source_updated_at == evidence.payload.committer_time
    assert evidence.provenance.response_sha256 == hash_payload(evidence.payload.payload())
    serialized = repr(evidence)
    assert "Test Author" not in serialized
    assert "author@example.com" not in serialized
    assert "secret" not in serialized


def test_git_connector_does_not_invoke_external_diff_helper(tmp_path: Path):
    root, commit = _repo(tmp_path)
    marker = tmp_path / "external-diff-ran"
    helper = tmp_path / "external-diff"
    helper.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    helper.chmod(0o755)
    _git(root, "config", "diff.external", str(helper))

    GitMetadataConnector().collect(
        GitMetadataRequest(RepoState(str(root), "target", commit=commit))
    )

    assert not marker.exists()


def test_git_connector_rejects_untrusted_revision_before_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    root, _ = _repo(tmp_path)
    connector = GitMetadataConnector()
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("git must not run")

    monkeypatch.setattr(connector, "_run", fail_if_called)

    with pytest.raises(ValueError, match="7 to 40 hexadecimal"):
        connector.collect(
            GitMetadataRequest(RepoState(str(root), "target", commit="HEAD; touch pwned"))
        )
    assert not called


def test_git_connector_enforces_hard_diff_output_limit(tmp_path: Path):
    root = tmp_path / "large-repo"
    root.mkdir()
    _git(root, "init", "-q")
    for index in range(100):
        (root / f"evidence-{index:03d}-with-a-long-path.txt").write_text("value\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "large changed-path set")
    commit = _git(root, "rev-parse", "HEAD")
    connector = GitMetadataConnector(max_output_bytes=1024)

    with pytest.raises(RuntimeError, match="output exceeded"):
        connector.collect(GitMetadataRequest(RepoState(str(root), "target", commit=commit)))


@pytest.mark.parametrize("unsafe_store", ["partial", "include", "alternates"])
def test_git_connector_rejects_network_capable_or_external_object_stores(
    tmp_path: Path, unsafe_store: str
):
    root, commit = _repo(tmp_path)
    if unsafe_store == "partial":
        _git(root, "config", "extensions.partialClone", "origin")
    elif unsafe_store == "include":
        _git(root, "config", "include.path", str(tmp_path / "untrusted.gitconfig"))
    else:
        alternates = root / ".git" / "objects" / "info" / "alternates"
        alternates.parent.mkdir(parents=True, exist_ok=True)
        alternates.write_text("/untrusted/object/store\n")

    with pytest.raises(ValueError, match="partial/promisor|included|alternates"):
        GitMetadataConnector().collect(
            GitMetadataRequest(RepoState(str(root), "target", commit=commit))
        )


def test_git_connector_reports_host_process_security_truthfully():
    security = GitMetadataConnector().descriptor.security

    assert security.source_access == "read_only"
    assert security.isolation == "host_subprocess"
    assert security.network_access == "host_unrestricted"
    assert security.credential_access == "ambient_host"


def test_git_connector_rejects_symlinked_object_store(tmp_path: Path):
    root, commit = _repo(tmp_path)
    objects = root / ".git" / "objects"
    external = tmp_path / "external-objects"
    objects.rename(external)
    objects.symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError, match="objects directory"):
        GitMetadataConnector().collect(
            GitMetadataRequest(RepoState(str(root), "target", commit=commit))
        )


def test_git_connector_rejects_non_regular_config_without_blocking(tmp_path: Path):
    root, commit = _repo(tmp_path)
    config = root / ".git" / "config"
    config.unlink()
    os.mkfifo(config)

    with pytest.raises(ValueError, match="regular local file"):
        GitMetadataConnector().collect(
            GitMetadataRequest(RepoState(str(root), "target", commit=commit))
        )


def test_git_connector_ignores_replacement_refs(tmp_path: Path):
    root, original = _repo(tmp_path)
    (root / "second.txt").write_text("replacement target\n")
    _git(root, "add", "--", "second.txt")
    _git(root, "commit", "-q", "-m", "second commit")
    replacement = _git(root, "rev-parse", "HEAD")
    _git(root, "replace", original, replacement)
    original_tree = _git(root, "--no-replace-objects", "rev-parse", f"{original}^{{tree}}")

    evidence = GitMetadataConnector().collect(
        GitMetadataRequest(RepoState(str(root), "target", commit=original))
    )

    assert evidence.payload.commit == original
    assert evidence.payload.tree == original_tree
    assert [change.path for change in evidence.payload.changes] == ["odd\tname.txt"]


def test_git_connector_defines_merge_diff_as_first_parent(tmp_path: Path):
    root, initial = _repo(tmp_path)
    primary_branch = _git(root, "branch", "--show-current")
    _git(root, "checkout", "-q", "-b", "side", initial)
    (root / "side.txt").write_text("side\n")
    _git(root, "add", "--", "side.txt")
    _git(root, "commit", "-q", "-m", "side")
    _git(root, "checkout", "-q", primary_branch)
    (root / "main.txt").write_text("main\n")
    _git(root, "add", "--", "main.txt")
    _git(root, "commit", "-q", "-m", "main")
    _git(root, "merge", "-q", "--no-ff", "side", "-m", "merge")
    merge = _git(root, "rev-parse", "HEAD")

    metadata = (
        GitMetadataConnector()
        .collect(GitMetadataRequest(RepoState(str(root), "target", commit=merge)))
        .payload
    )

    assert len(metadata.parents) == 2
    assert metadata.diff_base == metadata.parents[0]
    assert [change.path for change in metadata.changes] == ["side.txt"]
