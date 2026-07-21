from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from exhibit_a.executor.base import EnvironmentSetupError, RepoState
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
