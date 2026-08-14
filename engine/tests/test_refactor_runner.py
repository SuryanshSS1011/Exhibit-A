from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

import exhibit_a.verdict.refactor_runner as refactor_runner
from exhibit_a.connectors import LocalTestConnector
from exhibit_a.executor.base import ExecOutcome, ExecSpec, Executor, RepoState
from exhibit_a.executor.local_exec import LocalExecutor
from exhibit_a.models.case import ExecutionTruth, GoalTruth, ReleaseTruth, Verdict
from exhibit_a.verdict.refactor_runner import (
    CONTRACT_COMMAND,
    CONTRACT_PATH,
    EVIDENCE_SCHEMA,
    collect_refactor_evidence,
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


def test_runner_emits_one_validated_receipt_per_state_run():
    executor = RecordingExecutor()
    base = RepoState(
        "/secret/base",
        "base",
        commit="a" * 40,
        source="https://user:token@example.com/org/repo.git?credential=secret",
    )
    target = RepoState(
        "/secret/target",
        "target",
        commit="b" * 40,
        source="https://user:token@example.com/org/repo.git?credential=secret",
    )

    evidence = collect_refactor_evidence(
        executor,
        base,
        target,
        "def test_contract(): assert True\n",
    )

    assert evidence.schema_version == EVIDENCE_SCHEMA
    assert len(evidence.runs) == len(evidence.evidence_sources) == 6
    assert [run.state for run in evidence.runs] == ["base"] * 3 + ["target"] * 3
    assert [run.ordinal for run in evidence.runs] == [1, 2, 3, 1, 2, 3]
    assert {run.evidence_id for run in evidence.runs} == {
        source.evidence_id for source in evidence.evidence_sources
    }
    assert {source.artifact_sha256 for source in evidence.evidence_sources} == {
        evidence.contract_sha256
    }
    assert {source.source_revision for source in evidence.evidence_sources} == {
        "a" * 40,
        "b" * 40,
    }
    assert {source.source for source in evidence.evidence_sources} == {
        "https://example.com/org/repo.git"
    }
    encoded = json.dumps(evidence.to_dict(), sort_keys=True)
    assert '"claim_type": "behavior_preserving_refactor"' in encoded
    assert "/secret/" not in encoded
    assert "token" not in encoded
    assert "credential=secret" not in encoded


def test_runner_emits_no_result_when_a_later_receipt_is_invalid(monkeypatch):
    class TamperingConnector(LocalTestConnector):
        def __init__(self, executor):
            super().__init__(executor)
            self.calls = 0

        def collect(self, request):
            self.calls += 1
            output = super().collect(request)
            if self.calls == 4:
                return replace(
                    output,
                    provenance=replace(output.provenance, response_sha256="0" * 64),
                )
            return output

    executor = RecordingExecutor()
    monkeypatch.setattr(refactor_runner, "LocalTestConnector", TamperingConnector)

    with pytest.raises(ValueError, match="does not cover"):
        collect_refactor_evidence(
            executor,
            RepoState("/base", "base"),
            RepoState("/target", "target"),
            "def test_contract(): assert True\n",
        )

    assert len(executor.runs) == 4


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
