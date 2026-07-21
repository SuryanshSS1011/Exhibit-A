from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from exhibit_a.executor.base import ExecOutcome, ExecSpec, Executor, RepoState
from exhibit_a.studies import archaeology
from exhibit_a.studies.archaeology import Attribution, RevisionStatus, run_archaeology

REPO = "https://github.com/example/project.git"
SHAS = ["a" * 40, "b" * 40, "c" * 40]


class TimelineExecutor(Executor):
    def __init__(self, passing: set[str]):
        self.passing = passing

    def prepare(self, repo: RepoState) -> None:
        return None

    def run(self, repo: RepoState, spec: ExecSpec) -> ExecOutcome:
        if repo.commit in self.passing:
            return ExecOutcome(0, "1 passed", "")
        return ExecOutcome(1, "", "E   AssertionError: historical bug")


def _case(tmp_path: Path) -> Path:
    path = tmp_path / "case.json"
    path.write_text(
        json.dumps(
            {
                "id": "case-archaeology",
                "verdict": "PROVEN",
                "test_file": {
                    "path": "test_repro.py",
                    "code": "def test_repro(): assert False\n",
                },
                "run_command": "python3 -m pytest -x -q test_repro.py",
                "evidence": {"fail_signature": "AssertionError"},
            }
        )
    )
    return path


@contextmanager
def _checkout(repo_url: str, sha: str, *, label: str):
    yield RepoState("unused", label, commit=sha, source=repo_url)


def test_archaeology_finds_oldest_to_newest_break_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(archaeology, "checkout_context", _checkout)

    report = run_archaeology(_case(tmp_path), REPO, SHAS, TimelineExecutor({SHAS[0]}), reruns=2)

    assert report.attribution is Attribution.INTRODUCED
    assert report.boundary == (SHAS[0], SHAS[1])
    assert [item.status for item in report.observations] == [
        RevisionStatus.PASS,
        RevisionStatus.FAIL_MATCH,
        RevisionStatus.FAIL_MATCH,
    ]


def test_archaeology_marks_failure_before_observation_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(archaeology, "checkout_context", _checkout)

    report = run_archaeology(_case(tmp_path), REPO, SHAS, TimelineExecutor(set()), reruns=1)

    assert report.attribution is Attribution.PRE_EXISTING
    assert report.boundary is None


def test_archaeology_validates_every_sha_before_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    called = False

    @contextmanager
    def forbidden_checkout(repo_url: str, sha: str, *, label: str):
        nonlocal called
        called = True
        yield RepoState("unused", label)

    monkeypatch.setattr(archaeology, "checkout_context", forbidden_checkout)

    with pytest.raises(ValueError, match="commit SHA"):
        run_archaeology(
            _case(tmp_path), REPO, [SHAS[0], "main; touch /tmp/pwned"], TimelineExecutor(set())
        )

    assert not called
