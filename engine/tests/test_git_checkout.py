from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from exhibit_a.intake import git_checkout

REPO_URL = "https://github.com/example/project.git"
SHA = "abc1234def567890"
PUBLIC_ADDRESS = "140.82.121.4"


@pytest.fixture(autouse=True)
def offline_resolver(monkeypatch: pytest.MonkeyPatch):
    """Keep the suite offline; tests that care about resolution override this."""
    monkeypatch.setattr(git_checkout, "_resolve", lambda hostname: [PUBLIC_ADDRESS])


def test_checkout_rejects_untrusted_sha_before_git(monkeypatch: pytest.MonkeyPatch):
    called = False

    def fail_if_called(argv: list[str]) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(git_checkout, "_run_git", fail_if_called)

    with pytest.raises(ValueError, match="7 to 40 hexadecimal"):
        git_checkout.checkout(REPO_URL, "main; touch /tmp/pwned")

    assert not called


@pytest.mark.parametrize(
    "repo_url",
    ["file:///tmp/repo", "ext::sh -c evil", "https://user:secret@example.com/repo.git"],
)
def test_checkout_rejects_unsafe_repo_urls(repo_url: str):
    with pytest.raises(ValueError, match="HTTPS URL"):
        git_checkout.checkout(repo_url, SHA)


def test_checkout_uses_argv_disables_hooks_and_fetches_only_sha(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[list[str]] = []

    def fake_git(argv: list[str]) -> None:
        calls.append(argv)
        if "clone" in argv:
            Path(argv[-1]).mkdir()

    monkeypatch.setattr(git_checkout, "_run_git", fake_git)

    state = git_checkout.checkout(REPO_URL, SHA)
    try:
        assert state.commit == SHA
        assert state.label == "checkout"
        assert len(calls) == 3
        assert calls[0][:4] == ["git", "-c", "core.hooksPath=/dev/null", "clone"]
        assert calls[0][-2:] == [REPO_URL, state.path]
        assert calls[1][-2:] == ["origin", SHA]
        assert calls[2][-2:] == ["--detach", SHA]
        assert all(isinstance(call, list) for call in calls)
        assert all("core.hooksPath=/dev/null" in call for call in calls)
    finally:
        scratch = Path(state.path).parent
        git_checkout.cleanup(state)
        assert not scratch.exists()


def test_checkout_context_cleans_up_after_failure(monkeypatch: pytest.MonkeyPatch):
    def fake_git(argv: list[str]) -> None:
        if "clone" in argv:
            Path(argv[-1]).mkdir()

    monkeypatch.setattr(git_checkout, "_run_git", fake_git)

    with pytest.raises(RuntimeError, match="investigation failed"):
        with git_checkout.checkout_context(REPO_URL, SHA, label="target") as state:
            scratch = Path(state.path).parent
            assert state.label == "target"
            raise RuntimeError("investigation failed")

    assert not scratch.exists()


def test_checkout_triplet_labels_and_cleans_all_states(monkeypatch: pytest.MonkeyPatch):
    def fake_git(argv: list[str]) -> None:
        if "clone" in argv:
            Path(argv[-1]).mkdir()

    monkeypatch.setattr(git_checkout, "_run_git", fake_git)

    with git_checkout.checkout_triplet(REPO_URL, SHA, "def5678", "fedcba9") as states:
        paths = [Path(state.path).parent for state in states]
        assert [state.label for state in states] == ["target", "base", "control"]

    assert all(not path.exists() for path in paths)


@pytest.mark.parametrize(
    "repo_url",
    [
        "https://127.0.0.1/repo.git",
        "https://169.254.169.254/latest/meta-data/",
        "https://10.0.0.5/repo.git",
        "https://192.168.1.10/repo.git",
        "https://[::1]/repo.git",
    ],
)
def test_checkout_rejects_literal_private_hosts(repo_url: str):
    with pytest.raises(ValueError, match="public address"):
        git_checkout.checkout(repo_url, SHA)


def test_checkout_rejects_a_name_resolving_into_a_private_range(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(git_checkout, "_resolve", lambda hostname: ["127.0.0.1"])

    with pytest.raises(ValueError, match="public address"):
        git_checkout.checkout("https://rebound.invalid/repo.git", SHA)


def test_checkout_rejects_a_name_resolving_to_a_public_and_private_mix(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(git_checkout, "_resolve", lambda hostname: [PUBLIC_ADDRESS, "10.0.0.5"])

    with pytest.raises(ValueError, match="public address"):
        git_checkout.checkout("https://split.invalid/repo.git", SHA)


def test_checkout_rejects_an_ipv4_mapped_loopback(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(git_checkout, "_resolve", lambda hostname: ["::ffff:127.0.0.1"])

    with pytest.raises(ValueError, match="public address"):
        git_checkout.checkout("https://mapped.invalid/repo.git", SHA)


def test_checkout_rejects_a_host_that_resolves_to_nothing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(git_checkout, "_resolve", lambda hostname: [])

    with pytest.raises(ValueError, match="does not resolve"):
        git_checkout.checkout("https://missing.invalid/repo.git", SHA)


def test_git_commands_carry_a_wall_clock_timeout(monkeypatch: pytest.MonkeyPatch):
    """A wedged or hostile remote must not hang the investigation forever."""
    seen: list[object] = []

    def fake_run(argv, **kwargs):
        seen.append(kwargs.get("timeout"))
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr(git_checkout.subprocess, "run", fake_run)

    with pytest.raises(subprocess.TimeoutExpired):
        git_checkout.checkout(REPO_URL, SHA)

    assert seen == [git_checkout._GIT_TIMEOUT_S]
    assert git_checkout._GIT_TIMEOUT_S > 0
