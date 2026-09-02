"""Deterministic release truth derived from normalized CI status evidence.

This evaluator is deliberately separate from claim judges. It can describe whether a
pinned revision satisfies a bounded CI policy, but it cannot admit evidence or change a
claim verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from ..connectors.base import EvidenceKind, EvidenceProvenance, Freshness, hash_payload
from ..connectors.ci_status import CICheckRun, CIStatus
from ..models.case import ReleaseTruth

SCHEMA_VERSION = "release-policy/v1"

_FULL_SHA = re.compile(r"[0-9a-f]{40}")
_MAX_REQUIRED_CHECKS = 128
_MAX_AGE_S = 31 * 24 * 60 * 60
_FAILURE_CONCLUSIONS = frozenset(
    {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}
)


@dataclass(frozen=True)
class ReleasePolicy:
    """One strict, versioned policy over exact case-sensitive CI check names."""

    required_checks: tuple[str, ...]
    max_age_s: int
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("release policy schema version is unsupported")
        if not self.required_checks or len(self.required_checks) > _MAX_REQUIRED_CHECKS:
            raise ValueError("release policy requires between 1 and 128 checks")
        if len(set(self.required_checks)) != len(self.required_checks):
            raise ValueError("release policy check names must be unique")
        for name in self.required_checks:
            if not isinstance(name, str) or not name.strip() or name != name.strip():
                raise ValueError("release policy check name is invalid")
            if len(name) > 256:
                raise ValueError("release policy check name is invalid")
        if (
            not isinstance(self.max_age_s, int)
            or isinstance(self.max_age_s, bool)
            or not 1 <= self.max_age_s <= _MAX_AGE_S
        ):
            raise ValueError("release policy max age is out of range")


@dataclass(frozen=True)
class ReleaseAssessment:
    """A release-only conclusion that carries no claim-verdict authority."""

    release: ReleaseTruth
    reason: str
    policy_schema_version: str
    revision: str | None
    evaluated_at: str
    observed_at: str | None
    required_checks: tuple[str, ...]


def evaluate_release_policy(
    *,
    status: CIStatus | None,
    provenance: EvidenceProvenance | None,
    policy: ReleasePolicy,
    evaluated_at: datetime,
) -> ReleaseAssessment:
    """Evaluate one normalized point-in-time CI observation without side effects."""
    evaluated = _aware_utc(evaluated_at, "release policy evaluation time")
    if status is None and provenance is None:
        return _assessment(
            ReleaseTruth.NOT_ASSESSED,
            "no CI status evidence was supplied",
            policy,
            evaluated,
            None,
            None,
        )
    if status is None or provenance is None:
        return _assessment(
            ReleaseTruth.UNCERTAIN,
            "CI status payload and provenance must be supplied together",
            policy,
            evaluated,
            status.revision if status is not None else None,
            provenance.observed_at if provenance is not None else None,
        )

    issue = _evidence_issue(status, provenance)
    if issue is not None:
        return _assessment(
            ReleaseTruth.UNCERTAIN,
            issue,
            policy,
            evaluated,
            status.revision,
            provenance.observed_at,
        )

    observed = _parse_aware_utc(provenance.observed_at)
    if observed is None:
        return _uncertain("CI observation time is invalid", status, provenance, policy, evaluated)
    observation_age = (evaluated - observed).total_seconds()
    if observation_age < 0:
        return _uncertain(
            "CI observation time is in the future", status, provenance, policy, evaluated
        )
    if observation_age > policy.max_age_s:
        return _uncertain(
            "CI status observation is older than the policy allows",
            status,
            provenance,
            policy,
            evaluated,
        )
    if status.reported_total != len(status.checks):
        return _uncertain(
            "CI status observation is incomplete",
            status,
            provenance,
            policy,
            evaluated,
        )

    names = [check.name for check in status.checks]
    duplicate_names = sorted({name for name in names if names.count(name) > 1})
    if duplicate_names:
        return _uncertain(
            f"CI status contains duplicate check names: {_join(duplicate_names)}",
            status,
            provenance,
            policy,
            evaluated,
        )

    checks = {check.name: check for check in status.checks}
    missing = [name for name in policy.required_checks if name not in checks]
    if missing:
        return _uncertain(
            f"required CI checks are missing: {_join(missing)}",
            status,
            provenance,
            policy,
            evaluated,
        )

    failures: list[str] = []
    indeterminate: list[str] = []
    for name in policy.required_checks:
        check = checks[name]
        timing_issue = _completion_issue(check, observed, evaluated, policy.max_age_s)
        if timing_issue is not None:
            indeterminate.append(f"{name} ({timing_issue})")
            continue
        if check.status != "completed":
            indeterminate.append(f"{name} ({check.status})")
        elif check.conclusion == "success":
            continue
        elif check.conclusion in _FAILURE_CONCLUSIONS:
            failures.append(f"{name} ({check.conclusion})")
        else:
            indeterminate.append(f"{name} ({check.conclusion or 'no conclusion'})")

    if failures:
        return _assessment(
            ReleaseTruth.UNSAFE,
            f"required CI checks failed: {_join(failures)}",
            policy,
            evaluated,
            status.revision,
            provenance.observed_at,
        )
    if indeterminate:
        return _uncertain(
            f"required CI checks are not conclusively successful: {_join(indeterminate)}",
            status,
            provenance,
            policy,
            evaluated,
        )
    return _assessment(
        ReleaseTruth.SAFE,
        f"all required CI checks completed successfully: {_join(policy.required_checks)}",
        policy,
        evaluated,
        status.revision,
        provenance.observed_at,
    )


def _evidence_issue(status: CIStatus, provenance: EvidenceProvenance) -> str | None:
    if not isinstance(status.repository, str) or not status.repository.strip():
        return "CI status repository is invalid"
    if not isinstance(status.revision, str) or not _FULL_SHA.fullmatch(status.revision):
        return "CI status revision is not a full lowercase SHA-1"
    if provenance.capability is not EvidenceKind.CI_STATUS:
        return "CI evidence provenance has the wrong capability"
    if provenance.freshness is not Freshness.POINT_IN_TIME:
        return "CI evidence provenance has the wrong freshness basis"
    if provenance.source_revision != status.revision:
        return "CI evidence revision does not match its payload"
    if provenance.response_sha256 != hash_payload(status.payload()):
        return "CI evidence response digest does not match its payload"
    expected_content_sha256 = hash_payload(
        {
            "request_sha256": provenance.request_sha256,
            "response_sha256": provenance.response_sha256,
        }
    )
    if provenance.content_sha256 != expected_content_sha256:
        return "CI evidence content digest is inconsistent"
    if (
        not isinstance(status.reported_total, int)
        or isinstance(status.reported_total, bool)
        or status.reported_total < 0
        or not isinstance(status.checks, tuple)
    ):
        return "CI status summary is invalid"
    for check in status.checks:
        if not isinstance(check, CICheckRun):
            return "CI status check is invalid"
        if (
            not isinstance(check.name, str)
            or not check.name.strip()
            or check.name != check.name.strip()
            or len(check.name) > 256
        ):
            return "CI status check name is invalid"
        if not isinstance(check.status, str) or not check.status:
            return "CI status check state is invalid"
        if check.conclusion is not None and not isinstance(check.conclusion, str):
            return "CI status check conclusion is invalid"
    return None


def _completion_issue(
    check: CICheckRun,
    observed: datetime,
    evaluated: datetime,
    max_age_s: int,
) -> str | None:
    if check.status != "completed":
        return None
    completed = _parse_aware_utc(check.completed_at)
    if completed is None:
        return "invalid completion time"
    if completed > observed or completed > evaluated:
        return "completion time is in the future"
    if (evaluated - completed).total_seconds() > max_age_s:
        return "completion is stale"
    return None


def _uncertain(
    reason: str,
    status: CIStatus,
    provenance: EvidenceProvenance,
    policy: ReleasePolicy,
    evaluated: datetime,
) -> ReleaseAssessment:
    return _assessment(
        ReleaseTruth.UNCERTAIN,
        reason,
        policy,
        evaluated,
        status.revision,
        provenance.observed_at,
    )


def _assessment(
    release: ReleaseTruth,
    reason: str,
    policy: ReleasePolicy,
    evaluated: datetime,
    revision: str | None,
    observed_at: str | None,
) -> ReleaseAssessment:
    return ReleaseAssessment(
        release=release,
        reason=reason,
        policy_schema_version=policy.schema_version,
        revision=revision,
        evaluated_at=evaluated.isoformat(),
        observed_at=observed_at,
        required_checks=policy.required_checks,
    )


def _aware_utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_aware_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _join(values: tuple[str, ...] | list[str]) -> str:
    return ", ".join(values)
