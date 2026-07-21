from __future__ import annotations

import sys
from pathlib import Path

import pytest

from exhibit_a.executor.base import ExecOutcome, ExecSpec, Executor, RepoState, SourceMutation
from exhibit_a.executor.local_exec import LocalExecutor
from exhibit_a.verdict.mutation_testing import (
    MutationStatus,
    discover_mutations,
    score_mutations,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _spec(code: str) -> ExecSpec:
    return ExecSpec(
        test_path="test_mutation_repro.py",
        test_code=code,
        command=f"{sys.executable} -m pytest -x -q test_mutation_repro.py",
    )


def test_discovery_is_deterministic_and_scoped_to_suspect_lines():
    repo = FIXTURES / "fixed_slice"

    first = discover_mutations(repo, ["slicer.py"], suspect_lines={"slicer.py": {9}})
    second = discover_mutations(repo, ["slicer.py"], suspect_lines={"slicer.py": {9}})

    assert first == second
    assert len(first) == 1
    assert first[0].id == "slicer.py:9:17:-->+"
    assert first[0].original == "-"
    assert first[0].replacement == "+"
    assert discover_mutations(repo, ["slicer.py"], suspect_lines={"slicer.py": {8}}) == ()


def test_strong_candidate_kills_mutant_without_touching_source_tree():
    repo_path = FIXTURES / "fixed_slice"
    original = (repo_path / "slicer.py").read_text()
    mutations = discover_mutations(repo_path, ["slicer.py"])
    spec = _spec(
        "from slicer import last_n\n\n"
        "def test_last_one():\n"
        "    assert last_n([1, 2, 3, 4], 1) == [4]\n"
    )

    score = score_mutations(
        LocalExecutor(),
        RepoState(str(repo_path), "base"),
        spec,
        mutations,
        reruns=2,
    )

    assert score.baseline_passed
    assert score.generated == score.eligible == score.killed == 1
    assert score.survived == score.invalid == 0
    assert score.kill_rate == 1.0
    assert score.results[0].status is MutationStatus.KILLED
    assert (repo_path / "slicer.py").read_text() == original
    assert not (repo_path / spec.test_path).exists()


def test_weak_candidate_can_survive_without_changing_any_verdict():
    repo_path = FIXTURES / "fixed_slice"
    mutations = discover_mutations(repo_path, ["slicer.py"])
    spec = _spec(
        "from slicer import last_n\n\n"
        "def test_last_two():\n"
        "    assert last_n([1, 2, 3, 4], 2) == [3, 4]\n"
    )

    score = score_mutations(
        LocalExecutor(),
        RepoState(str(repo_path), "base"),
        spec,
        mutations,
        reruns=2,
    )

    assert score.baseline_passed
    assert score.killed == 0
    assert score.survived == 1
    assert score.kill_rate == 0.0
    assert score.results[0].status is MutationStatus.SURVIVED


class BaselineFailsExecutor(Executor):
    def prepare(self, repo: RepoState) -> None:
        return None

    def run(self, repo: RepoState, spec: ExecSpec) -> ExecOutcome:
        return ExecOutcome(1, "", "E   AssertionError: baseline")

    def run_mutant(self, repo: RepoState, spec: ExecSpec, mutation: SourceMutation):
        raise AssertionError("mutant ran before a passing baseline was established")


def test_nonpassing_baseline_cannot_produce_a_mutation_score():
    mutation = SourceMutation("m", "app.py", 1, 7, 11, "True", "False")

    score = score_mutations(
        BaselineFailsExecutor(),
        RepoState("unused", "base"),
        _spec("from app import value\nassert value\n"),
        [mutation],
        reruns=2,
    )

    assert not score.baseline_passed
    assert score.kill_rate is None
    assert score.eligible == 0
    assert score.invalid == 1


@pytest.mark.parametrize("path", ["../app.py", "/tmp/app.py", "app.txt"])
def test_discovery_rejects_unsafe_source_paths(path: str):
    with pytest.raises(ValueError, match="unsafe mutation source path"):
        discover_mutations(FIXTURES / "fixed_slice", [path])


def test_discovery_skips_contextually_invalid_operator_mutants(tmp_path: Path):
    (tmp_path / "app.py").write_text("def collect(*items):\n    return items\n")

    assert discover_mutations(tmp_path, ["app.py"]) == ()


def test_executor_rejects_non_allowlisted_mutation_before_running_test():
    repo_path = FIXTURES / "fixed_slice"
    mutation = SourceMutation(
        "injection",
        "slicer.py",
        9,
        17,
        18,
        "-",
        "__import__('os')",
    )

    with pytest.raises(ValueError, match="allowlisted deterministic operator"):
        LocalExecutor().run_mutant(
            RepoState(str(repo_path), "base"),
            _spec("from slicer import last_n\nassert last_n([1], 1) == [1]\n"),
            mutation,
        )
