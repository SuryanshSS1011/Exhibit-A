from __future__ import annotations

import json
from pathlib import Path

import pytest

from exhibit_a.executor.base import (
    EnvironmentSetupError,
    ExecOutcome,
    ExecSpec,
    Executor,
    RepoState,
)
from exhibit_a.executor.instrumented import (
    RecordingExecutor,
    repository_shape,
    summarize_environment_attempts,
)


class SetupExecutor(Executor):
    def __init__(self, error: Exception | None = None):
        self.error = error

    def prepare(self, repo: RepoState) -> str:
        if self.error:
            raise self.error
        return "image:known"

    def run(self, repo: RepoState, spec: ExecSpec) -> ExecOutcome:
        return ExecOutcome(0, "passed", "")


def test_recording_executor_captures_success_without_changing_result(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "uv.lock").write_text("version = 1\n")
    records = tmp_path / "attempts"
    executor = RecordingExecutor(SetupExecutor(), records)

    result = executor.prepare(RepoState(str(repo), "target", "abc1234", "owner/repo"))

    assert result == "image:known"
    payload = json.loads(next(records.glob("*.json")).read_text())
    assert payload["schema_version"] == "environment-attempt/v1"
    assert payload["repo_shape"] == ["uv.lock"]
    assert payload["succeeded"] is True
    assert payload["repo_source"] == "owner/repo"


def test_recording_executor_records_and_reraises_setup_failure(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    records = tmp_path / "attempts"
    executor = RecordingExecutor(
        SetupExecutor(EnvironmentSetupError("lock resolution failed")), records
    )

    with pytest.raises(EnvironmentSetupError, match="lock resolution failed"):
        executor.prepare(RepoState(str(repo), "target"))

    payload = json.loads(next(records.glob("*.json")).read_text())
    assert payload["succeeded"] is False
    assert payload["error_type"] == "EnvironmentSetupError"
    assert payload["strategy"] == "no-supported-lock"


def test_environment_summary_estimates_recipe_success_rate(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    records = tmp_path / "attempts"
    state = RepoState(str(repo), "target")
    RecordingExecutor(SetupExecutor(), records).prepare(state)
    with pytest.raises(EnvironmentSetupError):
        RecordingExecutor(SetupExecutor(EnvironmentSetupError("failed")), records).prepare(state)

    summary = summarize_environment_attempts(records)

    assert summary["attempts"] == 2
    assert summary["recipes"] == [
        {
            "repo_shape": ["stdlib-or-unknown"],
            "strategy": "no-supported-lock",
            "attempts": 2,
            "successes": 1,
            "success_rate": 0.5,
        }
    ]


def test_repository_shape_is_bounded_to_known_markers(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='example'\n")
    (tmp_path / "secret.txt").write_text("not collected")

    assert repository_shape(tmp_path) == ("pyproject.toml",)
