"""Deterministic judge for behavior-preserving refactor claims."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from ..executor.base import ExecOutcome
from ..models.case import ExecutionTruth, GoalTruth, ReleaseTruth, Verdict
from .flip_check import detect_infra_failure, extract_signature


class SuiteStatus(str, Enum):
    NOT_RUN = "NOT_RUN"
    PASS = "PASS"
    FAIL = "FAIL"
    FLAKY = "FLAKY"
    INFRA = "INFRA"


@dataclass(frozen=True)
class SuiteObservation:
    status: SuiteStatus
    runs: int
    exit_codes: tuple[int, ...]
    failure_signature: str | None = None
    failure_fingerprint: tuple[str, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class RefactorCheckResult:
    verdict: Verdict
    execution: ExecutionTruth
    goal: GoalTruth
    release: ReleaseTruth
    reason: str
    deterministic: bool
    base: SuiteObservation
    target: SuiteObservation


def refactor_check(
    *,
    base_runs: Sequence[ExecOutcome],
    target_runs: Sequence[ExecOutcome],
    required_reruns: int = 3,
) -> RefactorCheckResult:
    """Compare trusted behavioral-contract executions across pre/post-refactor states."""
    if required_reruns < 2:
        raise ValueError("required_reruns must be at least 2")
    base = _observe("base", base_runs)
    target = _observe("target", target_runs)

    infra = next(
        (observation for observation in (base, target) if observation.status is SuiteStatus.INFRA),
        None,
    )
    if infra is not None:
        return _result(
            Verdict.UNCERTAIN,
            ExecutionTruth.FAILED,
            GoalTruth.UNCERTAIN,
            infra.reason or "contract execution failed for an infrastructure reason",
            False,
            base,
            target,
        )
    if not base_runs or not target_runs:
        return _result(
            Verdict.UNCERTAIN,
            ExecutionTruth.NOT_RUN,
            GoalTruth.UNCERTAIN,
            "both base and target contract executions are required",
            False,
            base,
            target,
        )
    if base.status is SuiteStatus.FLAKY or target.status is SuiteStatus.FLAKY:
        state = "base" if base.status is SuiteStatus.FLAKY else "target"
        return _result(
            Verdict.UNCERTAIN,
            ExecutionTruth.COMPLETED,
            GoalTruth.UNCERTAIN,
            f"{state} contract execution was nondeterministic",
            False,
            base,
            target,
        )
    if len(base_runs) < required_reruns or len(target_runs) < required_reruns:
        return _result(
            Verdict.UNCERTAIN,
            ExecutionTruth.COMPLETED,
            GoalTruth.UNCERTAIN,
            f"behavior comparison requires {required_reruns} runs per state",
            False,
            base,
            target,
        )
    if any(
        observation.status is SuiteStatus.FAIL
        and (not observation.failure_fingerprint or observation.failure_signature is None)
        for observation in (base, target)
    ):
        return _result(
            Verdict.UNCERTAIN,
            ExecutionTruth.COMPLETED,
            GoalTruth.UNCERTAIN,
            "opaque contract failures could not be compared safely",
            False,
            base,
            target,
        )

    if base.status is SuiteStatus.PASS and target.status is SuiteStatus.PASS:
        return _result(
            Verdict.VERIFIED,
            ExecutionTruth.COMPLETED,
            GoalTruth.VERIFIED,
            "the behavioral contract passed deterministically before and after the refactor",
            True,
            base,
            target,
        )
    if (
        base.status is SuiteStatus.FAIL
        and target.status is SuiteStatus.FAIL
        and base.failure_fingerprint
        and base.failure_fingerprint == target.failure_fingerprint
        and base.exit_codes[0] == target.exit_codes[0]
    ):
        return _result(
            Verdict.PARTIAL,
            ExecutionTruth.COMPLETED,
            GoalTruth.PARTIAL,
            "the same deterministic contract failure was observed in both states; behavior may be preserved, but the contract is not green",
            True,
            base,
            target,
        )
    return _result(
        Verdict.FAILED,
        ExecutionTruth.COMPLETED,
        GoalTruth.FAILED,
        "the deterministic behavioral-contract outcome changed across the refactor",
        True,
        base,
        target,
    )


def _observe(state: str, runs: Sequence[ExecOutcome]) -> SuiteObservation:
    if not runs:
        return SuiteObservation(SuiteStatus.NOT_RUN, 0, ())
    exit_codes = tuple(run.exit_code for run in runs)
    for run in runs:
        if not run.passed and (reason := detect_infra_failure(run)):
            reason = reason.replace("target run ", "run ").replace("target failed ", "run failed ")
            return SuiteObservation(
                SuiteStatus.INFRA,
                len(runs),
                exit_codes,
                extract_signature(run),
                _failure_fingerprint(run),
                f"{state} contract execution failed: {reason}",
            )
        if not run.passed and run.exit_code != 1:
            return SuiteObservation(
                SuiteStatus.INFRA,
                len(runs),
                exit_codes,
                extract_signature(run),
                _failure_fingerprint(run),
                f"{state} contract execution returned non-test exit code {run.exit_code}",
            )
    passed = tuple(run.passed for run in runs)
    if any(passed) and not all(passed):
        return SuiteObservation(
            SuiteStatus.FLAKY,
            len(runs),
            exit_codes,
            reason=f"{state} contract alternated between pass and fail",
        )
    if all(passed):
        return SuiteObservation(SuiteStatus.PASS, len(runs), exit_codes)
    signatures = tuple(extract_signature(run) for run in runs)
    fingerprints = tuple(_failure_fingerprint(run) for run in runs)
    if len(set(zip(exit_codes, fingerprints))) != 1:
        return SuiteObservation(
            SuiteStatus.FLAKY,
            len(runs),
            exit_codes,
            reason=f"{state} contract failure changed across reruns",
        )
    return SuiteObservation(
        SuiteStatus.FAIL,
        len(runs),
        exit_codes,
        signatures[0],
        fingerprints[0],
    )


_FAILURE_LINE = re.compile(r"^(?:E\s+.*|FAILED\s+\S+.*|ERROR\s+\S+.*)$")


def _failure_fingerprint(outcome: ExecOutcome) -> tuple[str, ...]:
    """Capture the complete stable pytest failure surface, excluding timing prose."""
    return tuple(
        normalized
        for line in outcome.log.replace("\r\n", "\n").splitlines()
        if _FAILURE_LINE.match(normalized := " ".join(line.strip().split()))
    )


def _result(
    verdict: Verdict,
    execution: ExecutionTruth,
    goal: GoalTruth,
    reason: str,
    deterministic: bool,
    base: SuiteObservation,
    target: SuiteObservation,
) -> RefactorCheckResult:
    return RefactorCheckResult(
        verdict=verdict,
        execution=execution,
        goal=goal,
        release=ReleaseTruth.NOT_ASSESSED,
        reason=reason,
        deterministic=deterministic,
        base=base,
        target=target,
    )
