from __future__ import annotations

from pathlib import Path

import pytest

from exhibit_a.executor.base import ExecOutcome, ExecSpec, Executor, RepoState
from exhibit_a.executor.local_exec import LocalExecutor
from exhibit_a.models.case import ExecutionTruth, GoalTruth, ReleaseTruth, Verdict
from exhibit_a.verdict.refactor_runner import (
    CONTRACT_COMMAND,
    CONTRACT_PATH,
    run_refactor_contract,
)

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "refactor_corpus" / "rename"


class RecordingExecutor(Executor):
    def __init__(self, outcomes: dict[str, ExecOutcome] | None = None):
        self.outcomes = outcomes or {
            "base": ExecOutcome(0, "1 passed", ""),
            "target": ExecOutcome(0, "1 passed", ""),
        }
        self.prepared: list[RepoState] = []
        self.runs: list[tuple[RepoState, ExecSpec]] = []

    def prepare(self, repo: RepoState) -> str:
        self.prepared.append(repo)
        return f"image:{repo.label}"

    def run(self, repo: RepoState, spec: ExecSpec) -> ExecOutcome:
        self.runs.append((repo, spec))
        return self.outcomes[repo.label]


def test_runner_uses_one_fixed_network_disabled_contract_per_state():
    executor = RecordingExecutor()
    base = RepoState("/base", "caller-base")
    target = RepoState("/target", "caller-target")

    result = run_refactor_contract(
        executor,
        base,
        target,
        "def test_contract(): assert True\n",
        reruns=3,
        timeout_s=17,
    )

    assert result.verdict is Verdict.VERIFIED
    assert [repo.label for repo in executor.prepared] == ["base", "target"]
    assert len(executor.runs) == 6
    for repo, spec in executor.runs:
        assert spec.test_path == CONTRACT_PATH
        assert spec.command == CONTRACT_COMMAND
        assert spec.timeout_s == 17
        assert not spec.network
        assert spec.image == f"image:{repo.label}"


def test_runner_reports_a_stable_behavior_change_as_failed():
    executor = RecordingExecutor(
        {
            "base": ExecOutcome(0, "1 passed", ""),
            "target": ExecOutcome(1, "", "E   AssertionError: assert 12 == 10"),
        }
    )

    result = run_refactor_contract(
        executor,
        RepoState("/base", "base"),
        RepoState("/target", "target"),
        "def test_contract(): assert True\n",
    )

    assert result.verdict is Verdict.FAILED
    assert result.execution is ExecutionTruth.COMPLETED
    assert result.goal is GoalTruth.FAILED
    assert result.release is ReleaseTruth.NOT_ASSESSED


@pytest.mark.parametrize(
    ("reruns", "timeout_s"),
    [(0, 120), (1, 120), (True, 120), (2.5, 120), (3, 0), (3, -1), (3, True), (3, 1.5)],
)
def test_runner_rejects_invalid_budgets_before_execution(reruns, timeout_s):
    executor = RecordingExecutor()

    with pytest.raises(ValueError):
        run_refactor_contract(
            executor,
            RepoState("/base", "base"),
            RepoState("/target", "target"),
            "def test_contract(): assert True\n",
            reruns=reruns,
            timeout_s=timeout_s,
        )

    assert executor.prepared == []
    assert executor.runs == []


def test_runner_rejects_non_text_contract_before_execution():
    executor = RecordingExecutor()

    with pytest.raises(TypeError, match="contract_code must be a string"):
        run_refactor_contract(
            executor,
            RepoState("/base", "base"),
            RepoState("/target", "target"),
            None,  # type: ignore[arg-type]
        )

    assert executor.prepared == []
    assert executor.runs == []


def test_runner_verifies_the_real_refactor_and_rejects_changed_behavior():
    contract_code = (CORPUS / "contract.py").read_text()
    base = RepoState(str(CORPUS / "base"), "base")
    target = RepoState(str(CORPUS / "target"), "target")
    changed = RepoState(str(CORPUS / "changed"), "target")
    executor = LocalExecutor()

    preserved = run_refactor_contract(executor, base, target, contract_code)
    altered = run_refactor_contract(executor, base, changed, contract_code)

    assert preserved.verdict is Verdict.VERIFIED
    assert preserved.goal is GoalTruth.VERIFIED
    assert altered.verdict is Verdict.FAILED
    assert altered.goal is GoalTruth.FAILED
    assert not (CORPUS / "base" / CONTRACT_PATH).exists()
    assert not (CORPUS / "target" / CONTRACT_PATH).exists()
    assert not (CORPUS / "changed" / CONTRACT_PATH).exists()
