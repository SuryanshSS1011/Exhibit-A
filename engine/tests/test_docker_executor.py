from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from exhibit_a.executor.base import EnvironmentSetupError, ExecSpec, RepoState, SourceMutation
from exhibit_a.executor.docker_exec import DockerExecutor, _environment_spec


def test_environment_requires_a_lockfile(tmp_path: Path):
    with pytest.raises(
        EnvironmentSetupError, match="dependency discovery is intentionally disabled"
    ):
        _environment_spec(RepoState(str(tmp_path), "target", source="repo-a"))


def test_requirements_must_be_pinned(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("requests>=2\n")

    with pytest.raises(EnvironmentSetupError, match="not a self-contained pinned"):
        _environment_spec(RepoState(str(tmp_path), "target", source="repo-a"))


def test_environment_cache_key_uses_repo_and_lock_content(tmp_path: Path):
    lock = tmp_path / "requirements.txt"
    lock.write_text("requests==2.32.4\n")
    first = _environment_spec(RepoState(str(tmp_path), "target", source="repo-a"))
    same = _environment_spec(RepoState(str(tmp_path), "base", source="repo-a"))
    other_repo = _environment_spec(RepoState(str(tmp_path), "target", source="repo-b"))
    lock.write_text("requests==2.32.5\n")
    other_lock = _environment_spec(RepoState(str(tmp_path), "target", source="repo-a"))

    assert first.image == same.image
    assert first.image != other_repo.image
    assert first.image != other_lock.image


def test_pipfile_lock_is_converted_to_exact_requirements(tmp_path: Path):
    (tmp_path / "Pipfile.lock").write_text(
        json.dumps({"default": {"requests": {"version": "==2.32.4"}}, "develop": {}})
    )

    spec = _environment_spec(RepoState(str(tmp_path), "target", source="repo-a"))

    assert spec.requirements == ("requests==2.32.4\n",)


def test_prepare_builds_with_argv_and_reuses_cached_image(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    (tmp_path / "requirements.txt").write_text("requests==2.32.4\n")
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        calls.append(argv)
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
    assert calls[0][:3] == ["docker", "image", "inspect"]
    assert calls[1][0:2] == ["docker", "build"]
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
