from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from exhibit_a.executor.base import EnvironmentSetupError, ExecSpec, RepoState, SourceMutation
from exhibit_a.executor.docker_exec import (
    DockerExecutor,
    _base_reference,
    _carries_hashes,
    _dockerfile,
    _environment_spec,
)

BASE_DIGEST = "python@sha256:" + "a" * 64


def _base_probe(argv: list[str]) -> subprocess.CompletedProcess | None:
    """Answer the base-image digest probe. None means the call is something else."""
    if argv[1:3] == ["image", "inspect"] and "--format" in argv:
        return subprocess.CompletedProcess(argv, 0, BASE_DIGEST + "\n", "")
    return None


def test_environment_requires_a_lockfile(tmp_path: Path):
    with pytest.raises(
        EnvironmentSetupError, match="dependency discovery is intentionally disabled"
    ):
        _environment_spec(
            RepoState(str(tmp_path), "target", source="repo-a"), base_reference=BASE_DIGEST
        )


def test_requirements_must_be_pinned(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("requests>=2\n")

    with pytest.raises(EnvironmentSetupError, match="not a self-contained pinned"):
        _environment_spec(
            RepoState(str(tmp_path), "target", source="repo-a"), base_reference=BASE_DIGEST
        )


def test_environment_cache_key_uses_repo_and_lock_content(tmp_path: Path):
    lock = tmp_path / "requirements.txt"
    lock.write_text("requests==2.32.4\n")
    first = _environment_spec(
        RepoState(str(tmp_path), "target", source="repo-a"), base_reference=BASE_DIGEST
    )
    same = _environment_spec(
        RepoState(str(tmp_path), "base", source="repo-a"), base_reference=BASE_DIGEST
    )
    other_repo = _environment_spec(
        RepoState(str(tmp_path), "target", source="repo-b"), base_reference=BASE_DIGEST
    )
    lock.write_text("requests==2.32.5\n")
    other_lock = _environment_spec(
        RepoState(str(tmp_path), "target", source="repo-a"), base_reference=BASE_DIGEST
    )

    assert first.image == same.image
    assert first.image != other_repo.image
    assert first.image != other_lock.image


def test_pipfile_lock_is_converted_to_exact_requirements(tmp_path: Path):
    (tmp_path / "Pipfile.lock").write_text(
        json.dumps({"default": {"requests": {"version": "==2.32.4"}}, "develop": {}})
    )

    spec = _environment_spec(
        RepoState(str(tmp_path), "target", source="repo-a"), base_reference=BASE_DIGEST
    )

    assert spec.requirements == ("requests==2.32.4\n",)


def test_prepare_builds_with_argv_and_reuses_cached_image(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    (tmp_path / "requirements.txt").write_text("requests==2.32.4\n")
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        calls.append(argv)
        probe = _base_probe(argv)
        if probe is not None:
            return probe
        if argv[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(argv, 1, "", "missing")
        dockerfile = Path(argv[argv.index("--file") + 1]).read_text()
        assert "requests==2.32.4" in (Path(argv[-1]) / "requirements-0.txt").read_text()
        assert "pip install" in dockerfile
        return subprocess.CompletedProcess(argv, 0, "built", "")

    monkeypatch.setattr("exhibit_a.executor.docker_exec.subprocess.run", fake_run)
    executor = DockerExecutor()
    image = executor.prepare(RepoState(str(tmp_path), "target", source="repo-a"))

    assert image and image.startswith("exhibit-a-env:")
    assert calls[0][1:3] == ["image", "inspect"] and "--format" in calls[0]
    assert calls[1][:3] == ["docker", "image", "inspect"]
    assert calls[2][0:2] == ["docker", "build"]
    assert all(isinstance(call, list) for call in calls)


def test_existing_suite_runs_in_read_only_no_network_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    (tmp_path / "module.py").write_text("VALUE = 1\n")
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "3 passed", "")

    monkeypatch.setattr("exhibit_a.executor.docker_exec.subprocess.run", fake_run)
    outcome = DockerExecutor().run_suite(
        RepoState(str(tmp_path), "target"),
        ["python3", "-m", "pytest", "-q"],
        image="exhibit-a-env:test",
    )

    assert outcome.passed
    argv = calls[0]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv
    assert argv[-4:] == ["python3", "-m", "pytest", "-q"]
    assert (tmp_path / "module.py").read_text() == "VALUE = 1\n"


def test_mutant_is_applied_only_to_disposable_read_only_container_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    source = tmp_path / "module.py"
    source.write_text("FLAG = True\n")

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        mount = argv[argv.index("-v") + 1]
        work = Path(mount.removesuffix(":/work:ro"))
        assert (work / "module.py").read_text() == "FLAG = False\n"
        assert (work / "test_repro.py").is_file()
        assert argv[argv.index("--network") + 1] == "none"
        assert "--read-only" in argv
        return subprocess.CompletedProcess(argv, 0, "1 passed", "")

    monkeypatch.setattr("exhibit_a.executor.docker_exec.subprocess.run", fake_run)
    outcome = DockerExecutor().run_mutant(
        RepoState(str(tmp_path), "base"),
        ExecSpec(
            "test_repro.py",
            "from module import FLAG\n\ndef test_flag(): assert not FLAG\n",
            "python3 -m pytest -q test_repro.py",
            image="exhibit-a-env:test",
        ),
        SourceMutation("flag", "module.py", 1, 7, 11, "True", "False"),
    )

    assert outcome.passed
    assert source.read_text() == "FLAG = True\n"


def test_base_digest_participates_in_the_environment_key(tmp_path: Path):
    """A moved base tag must produce a different image, not silently reuse the cache."""
    (tmp_path / "requirements.txt").write_text("requests==2.32.4\n")
    repo = RepoState(str(tmp_path), "target", source="repo-a")

    pinned = _environment_spec(repo, base_reference=BASE_DIGEST)
    moved = _environment_spec(repo, base_reference="python@sha256:" + "b" * 64)

    assert pinned.image != moved.image
    assert pinned.base_reference == BASE_DIGEST


def test_dockerfile_builds_from_the_digest_not_the_tag():
    dockerfile = _dockerfile(["requirements-0.txt"], BASE_DIGEST)

    assert dockerfile.startswith(f"FROM {BASE_DIGEST}\n")
    assert "FROM python:3.12-slim\n" not in dockerfile


def test_base_reference_pulls_once_when_the_image_is_absent(monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str]] = []
    present = {"value": False}

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        calls.append(argv)
        if argv[1] == "pull":
            present["value"] = True
            return subprocess.CompletedProcess(argv, 0, "", "")
        if present["value"]:
            return subprocess.CompletedProcess(argv, 0, BASE_DIGEST + "\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "No such image")

    monkeypatch.setattr("exhibit_a.executor.docker_exec.subprocess.run", fake_run)

    assert _base_reference("docker") == BASE_DIGEST
    assert [call[1] for call in calls] == ["image", "pull", "image"]


def test_base_reference_fails_closed_when_the_image_cannot_be_resolved(
    monkeypatch: pytest.MonkeyPatch,
):
    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(argv, 1, "", "offline")

    monkeypatch.setattr("exhibit_a.executor.docker_exec.subprocess.run", fake_run)

    with pytest.raises(EnvironmentSetupError, match="could not be pulled"):
        _base_reference("docker")


def test_hashes_are_enforced_when_the_lockfile_ships_them():
    """Ignoring pinned hashes would accept an index serving different bytes."""
    hashed = _dockerfile(["requirements-0.txt"], BASE_DIGEST, (True,))
    plain = _dockerfile(["requirements-0.txt"], BASE_DIGEST, (False,))

    assert "--require-hashes --requirement /tmp/locks/requirements-0.txt" in hashed
    assert "--require-hashes" not in plain


def test_hash_enforcement_is_decided_per_lockfile():
    dockerfile = _dockerfile(
        ["requirements-0.txt", "requirements-1.txt"], BASE_DIGEST, (False, True)
    )

    assert "--no-cache-dir --requirement /tmp/locks/requirements-0.txt" in dockerfile
    assert "--require-hashes --requirement /tmp/locks/requirements-1.txt" in dockerfile


def test_carries_hashes_detects_both_layouts():
    assert _carries_hashes("requests==2.32.4 --hash=sha256:abc\n")
    assert _carries_hashes("requests==2.32.4 \\\n    --hash=sha256:abc\n")
    assert not _carries_hashes("requests==2.32.4\n")


def _src_layout(root: Path) -> None:
    package = root / "src" / "mypkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n")


def test_src_layout_puts_its_source_root_on_the_container_pythonpath(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # Without this the candidate raises ModuleNotFoundError before it can exercise
    # anything, which is indistinguishable from a test that simply does not work.
    _src_layout(tmp_path)
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "1 passed", "")

    monkeypatch.setattr("exhibit_a.executor.docker_exec.subprocess.run", fake_run)
    DockerExecutor().run(
        RepoState(str(tmp_path), "target"),
        ExecSpec(
            test_path="test_repro.py",
            test_code="def test_x():\n    assert True\n",
            command="python3 -m pytest -q test_repro.py",
            image="exhibit-a-env:test",
        ),
    )

    argv = calls[0]
    assert f"PYTHONPATH={'/work/src'}" in argv
    # The mount and workdir still follow the environment flags, in that order.
    assert argv[argv.index("-v") + 1].endswith(":/work:ro")
    assert argv[argv.index("-w") + 1] == "/work"


def test_the_preflight_gets_the_same_import_environment_as_the_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # A suite that is red only because our sandbox could not import the project is a
    # statement about the sandbox. The recorded observation has to describe the repo.
    _src_layout(tmp_path)
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "3 passed", "")

    monkeypatch.setattr("exhibit_a.executor.docker_exec.subprocess.run", fake_run)
    DockerExecutor().run_suite(
        RepoState(str(tmp_path), "target"),
        ["python3", "-m", "pytest", "-q"],
        image="exhibit-a-env:test",
    )

    argv = calls[0]
    assert "PYTHONPATH=/work/src" in argv
    assert argv[-4:] == ["python3", "-m", "pytest", "-q"]


def test_a_flat_layout_sets_no_pythonpath_at_all(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # The working directory already imports it, so the common case is untouched.
    (tmp_path / "mypkg").mkdir()
    (tmp_path / "mypkg" / "__init__.py").write_text("")
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "1 passed", "")

    monkeypatch.setattr("exhibit_a.executor.docker_exec.subprocess.run", fake_run)
    DockerExecutor().run_suite(
        RepoState(str(tmp_path), "target"),
        ["python3", "-m", "pytest", "-q"],
        image="exhibit-a-env:test",
    )

    assert not any(item.startswith("PYTHONPATH=") for item in calls[0])
