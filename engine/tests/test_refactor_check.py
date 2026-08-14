from __future__ import annotations

import pytest

from exhibit_a.executor.base import ExecOutcome
from exhibit_a.models.case import ExecutionTruth, GoalTruth, ReleaseTruth, Verdict
from exhibit_a.verdict.refactor_check import SuiteStatus, refactor_check


def _pass() -> ExecOutcome:
    return ExecOutcome(0, "1 passed", "")


def _fail(signature: str = "AssertionError: 12 != 10") -> ExecOutcome:
    return ExecOutcome(1, "", f"E   {signature}")


def _runs(outcome: ExecOutcome, count: int = 3) -> list[ExecOutcome]:
    return [outcome for _ in range(count)]


def test_refactor_check_verifies_deterministic_green_contract_on_both_states():
    result = refactor_check(base_runs=_runs(_pass()), target_runs=_runs(_pass()))

    assert result.verdict is Verdict.VERIFIED
    assert result.execution is ExecutionTruth.COMPLETED
    assert result.goal is GoalTruth.VERIFIED
    assert result.release is ReleaseTruth.NOT_ASSESSED
    assert result.deterministic
    assert result.base.status is SuiteStatus.PASS
    assert result.target.status is SuiteStatus.PASS


def test_refactor_check_does_not_scan_passing_logs_for_failure_markers():
    passing_with_marker = ExecOutcome(0, "caught ImportError as expected", "")

    result = refactor_check(
        base_runs=_runs(passing_with_marker), target_runs=_runs(passing_with_marker)
    )

    assert result.verdict is Verdict.VERIFIED
    assert result.execution is ExecutionTruth.COMPLETED


@pytest.mark.parametrize(
    ("base", "target"),
    [
        (_runs(_pass()), _runs(_fail())),
        (_runs(_fail()), _runs(_pass())),
        (_runs(_fail("ValueError: before")), _runs(_fail("TypeError: after"))),
    ],
)
def test_refactor_check_fails_stable_cross_state_behavior_changes(base, target):
    result = refactor_check(base_runs=base, target_runs=target)

    assert result.verdict is Verdict.FAILED
    assert result.execution is ExecutionTruth.COMPLETED
    assert result.goal is GoalTruth.FAILED
    assert result.release is ReleaseTruth.NOT_ASSESSED
    assert result.deterministic


def test_refactor_check_keeps_identical_stable_failures_partial():
    result = refactor_check(base_runs=_runs(_fail()), target_runs=_runs(_fail()))

    assert result.verdict is Verdict.PARTIAL
    assert result.goal is GoalTruth.PARTIAL
    assert result.deterministic


def test_refactor_check_keeps_opaque_failures_uncertain():
    opaque_before = ExecOutcome(1, "", "before opaque failure")
    opaque_after = ExecOutcome(1, "", "after opaque failure")

    result = refactor_check(base_runs=_runs(opaque_before), target_runs=_runs(opaque_after))

    assert result.verdict is Verdict.UNCERTAIN
    assert result.goal is GoalTruth.UNCERTAIN
    assert "opaque" in result.reason


def test_refactor_check_keeps_reasonless_failure_summaries_uncertain():
    summary_only = ExecOutcome(1, "FAILED tests/test_contract.py::test_x", "")

    result = refactor_check(base_runs=_runs(summary_only), target_runs=_runs(summary_only))

    assert result.verdict is Verdict.UNCERTAIN
    assert result.goal is GoalTruth.UNCERTAIN


def test_refactor_check_detects_changed_secondary_failure():
    base = ExecOutcome(
        1,
        "",
        "E   AssertionError: first\nE   ValueError: second-before",
    )
    target = ExecOutcome(
        1,
        "",
        "E   AssertionError: first\nE   TypeError: second-after",
    )

    result = refactor_check(base_runs=_runs(base), target_runs=_runs(target))

    assert result.verdict is Verdict.FAILED
    assert result.goal is GoalTruth.FAILED


@pytest.mark.parametrize(
    ("base", "target", "reason"),
    [
        ([], _runs(_pass()), "both base and target"),
        (_runs(_pass(), 2), _runs(_pass(), 2), "requires 3 runs"),
        (_runs(_pass()), [_pass(), _fail(), _pass()], "nondeterministic"),
        (
            _runs(_pass()),
            [
                _fail("AssertionError: one"),
                _fail("AssertionError: two"),
                _fail("AssertionError: one"),
            ],
            "nondeterministic",
        ),
    ],
)
def test_refactor_check_stays_uncertain_without_deterministic_comparison(base, target, reason):
    result = refactor_check(base_runs=base, target_runs=target)

    assert result.verdict is Verdict.UNCERTAIN
    assert result.goal is GoalTruth.UNCERTAIN
    assert reason in result.reason
    assert result.release is ReleaseTruth.NOT_ASSESSED


@pytest.mark.parametrize(
    "broken",
    [
        ExecOutcome(124, "", "TIMEOUT", timed_out=True),
        ExecOutcome(2, "", "E   ModuleNotFoundError: missing"),
        ExecOutcome(2, "", "ERROR collecting test_contract.py"),
        ExecOutcome(3, "", "pytest internal error"),
        ExecOutcome(4, "", "pytest usage error"),
        ExecOutcome(5, "", "no tests collected"),
    ],
)
def test_refactor_check_treats_infrastructure_failure_as_uncertain(broken):
    result = refactor_check(base_runs=_runs(_pass()), target_runs=_runs(broken))

    assert result.verdict is Verdict.UNCERTAIN
    assert result.execution is ExecutionTruth.FAILED
    assert result.goal is GoalTruth.UNCERTAIN
    assert result.target.status is SuiteStatus.INFRA
    assert result.release is ReleaseTruth.NOT_ASSESSED


def test_refactor_check_reports_base_infrastructure_failure_accurately():
    timed_out = ExecOutcome(124, "", "TIMEOUT", timed_out=True)

    result = refactor_check(base_runs=_runs(timed_out), target_runs=_runs(_pass()))

    assert result.execution is ExecutionTruth.FAILED
    assert result.reason.startswith("base contract execution failed: run timed out")
    assert "target run" not in result.reason


@pytest.mark.parametrize("required_reruns", [0, 1])
def test_refactor_check_rejects_invalid_rerun_requirement(required_reruns):
    with pytest.raises(ValueError, match="must be at least 2"):
        refactor_check(base_runs=[], target_runs=[], required_reruns=required_reruns)
