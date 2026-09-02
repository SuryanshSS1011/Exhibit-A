from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from exhibit_a.connectors import (
    CICheckRun,
    CIStatus,
    ConnectorSecurity,
    EvidenceKind,
    EvidenceProvenance,
    Freshness,
)
from exhibit_a.connectors.base import hash_payload
from exhibit_a.models.case import Case, Mode, ReleaseTruth, Verdict
from exhibit_a.verdict.release_policy import (
    SCHEMA_VERSION,
    ReleasePolicy,
    evaluate_release_policy,
)

REVISION = "1f9473f8d6940935ec45a41cb518d9038e0bea0e"
OBSERVED = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
EVALUATED = OBSERVED + timedelta(minutes=5)


def _check(
    name: str,
    *,
    status: str = "completed",
    conclusion: str | None = "success",
    completed_at: datetime | None = OBSERVED - timedelta(minutes=1),
) -> CICheckRun:
    return CICheckRun(
        name=name,
        status=status,
        conclusion=conclusion,
        started_at=(OBSERVED - timedelta(minutes=3)).isoformat(),
        completed_at=completed_at.isoformat() if completed_at is not None else None,
    )


def _evidence(
    *checks: CICheckRun,
    reported_total: int | None = None,
    observed_at: datetime = OBSERVED,
    connector_id: str = "github_ci_status",
) -> tuple[CIStatus, EvidenceProvenance]:
    status = CIStatus(
        repository="SuryanshSS1011/Exhibit-A",
        revision=REVISION,
        reported_total=len(checks) if reported_total is None else reported_total,
        checks=tuple(checks),
    )
    request_sha256 = "b" * 64
    response_sha256 = hash_payload(status.payload())
    provenance = EvidenceProvenance(
        evidence_id="a" * 32,
        connector_id=connector_id,
        connector_version="1",
        capability=EvidenceKind.CI_STATUS,
        source="https://api.example.test/repos/SuryanshSS1011/Exhibit-A",
        source_revision=REVISION,
        observed_at=observed_at.isoformat(),
        source_updated_at=max(
            (check.completed_at for check in checks if check.completed_at), default=None
        ),
        freshness=Freshness.POINT_IN_TIME,
        description="Read read-only CI status for one commit",
        request_sha256=request_sha256,
        response_sha256=response_sha256,
        artifact_sha256="c" * 64,
        content_sha256=hash_payload(
            {
                "request_sha256": request_sha256,
                "response_sha256": response_sha256,
            }
        ),
        security=ConnectorSecurity(
            source_access="read_only",
            network_access="host_unrestricted",
            isolation="in_process",
        ),
    )
    return status, provenance


def _policy(*names: str, max_age_s: int = 3600) -> ReleasePolicy:
    return ReleasePolicy(required_checks=names, max_age_s=max_age_s)


def _evaluate(
    status: CIStatus | None,
    provenance: EvidenceProvenance | None,
    policy: ReleasePolicy | None = None,
):
    return evaluate_release_policy(
        status=status,
        provenance=provenance,
        policy=policy or _policy("engine", "web"),
        evaluated_at=EVALUATED,
    )


def test_all_required_checks_safely_satisfy_the_policy():
    status, provenance = _evidence(
        _check("engine"),
        _check("web"),
        _check("optional", conclusion="failure"),
    )

    assessment = _evaluate(status, provenance)

    assert assessment.release is ReleaseTruth.SAFE
    assert assessment.policy_schema_version == SCHEMA_VERSION
    assert assessment.revision == REVISION
    assert assessment.required_checks == ("engine", "web")
    assert assessment.evaluated_at == EVALUATED.isoformat()
    assert assessment.observed_at == OBSERVED.isoformat()
    assert "engine, web" in assessment.reason


@pytest.mark.parametrize(
    "conclusion",
    ["failure", "cancelled", "timed_out", "action_required", "startup_failure"],
)
def test_fresh_required_check_failures_are_unsafe(conclusion: str):
    status, provenance = _evidence(_check("engine", conclusion=conclusion), _check("web"))

    assessment = _evaluate(status, provenance)

    assert assessment.release is ReleaseTruth.UNSAFE
    assert f"engine ({conclusion})" in assessment.reason


@pytest.mark.parametrize(
    ("status_value", "conclusion"),
    [
        ("queued", None),
        ("in_progress", None),
        ("waiting", None),
        ("future_backend_state", None),
        ("completed", None),
        ("completed", "neutral"),
        ("completed", "skipped"),
        ("completed", "stale"),
        ("completed", "future_conclusion"),
    ],
)
def test_pending_or_non_successful_required_checks_are_uncertain(
    status_value: str, conclusion: str | None
):
    status, provenance = _evidence(
        _check("engine", status=status_value, conclusion=conclusion),
        _check("web"),
    )

    assessment = _evaluate(status, provenance)

    assert assessment.release is ReleaseTruth.UNCERTAIN
    assert "engine" in assessment.reason


def test_missing_duplicate_and_incomplete_observations_are_uncertain():
    missing_status, missing_provenance = _evidence(_check("engine"))
    duplicate_status, duplicate_provenance = _evidence(
        _check("engine"), _check("engine"), _check("web")
    )
    incomplete_status, incomplete_provenance = _evidence(
        _check("engine"), _check("web"), reported_total=3
    )

    missing = _evaluate(missing_status, missing_provenance)
    duplicate = _evaluate(duplicate_status, duplicate_provenance)
    incomplete = _evaluate(incomplete_status, incomplete_provenance)

    assert missing.release is ReleaseTruth.UNCERTAIN
    assert "missing" in missing.reason
    assert duplicate.release is ReleaseTruth.UNCERTAIN
    assert "duplicate" in duplicate.reason
    assert incomplete.release is ReleaseTruth.UNCERTAIN
    assert "incomplete" in incomplete.reason


def test_no_evidence_is_not_assessed_but_a_partial_pair_is_uncertain():
    status, _provenance = _evidence(_check("engine"), _check("web"))

    absent = _evaluate(None, None)
    partial = _evaluate(status, None)

    assert absent.release is ReleaseTruth.NOT_ASSESSED
    assert partial.release is ReleaseTruth.UNCERTAIN


def test_stale_or_future_observations_are_uncertain():
    stale_status, stale_provenance = _evidence(
        _check("engine"),
        _check("web"),
        observed_at=EVALUATED - timedelta(hours=2),
    )
    future_status, future_provenance = _evidence(
        _check("engine"),
        _check("web"),
        observed_at=EVALUATED + timedelta(seconds=1),
    )

    stale = _evaluate(stale_status, stale_provenance)
    future = _evaluate(future_status, future_provenance)

    assert stale.release is ReleaseTruth.UNCERTAIN
    assert "older" in stale.reason
    assert future.release is ReleaseTruth.UNCERTAIN
    assert "future" in future.reason


@pytest.mark.parametrize(
    "completed_at",
    [None, EVALUATED - timedelta(hours=2), EVALUATED + timedelta(seconds=1)],
)
def test_invalid_or_stale_required_check_completion_is_uncertain(
    completed_at: datetime | None,
):
    status, provenance = _evidence(
        _check("engine", completed_at=completed_at),
        _check("web"),
    )

    assessment = _evaluate(status, provenance)

    assert assessment.release is ReleaseTruth.UNCERTAIN
    assert "engine" in assessment.reason


def test_mismatched_or_tampered_provenance_is_uncertain():
    status, provenance = _evidence(_check("engine"), _check("web"))

    mismatched = _evaluate(
        status,
        replace(provenance, source_revision="0" * 40),
    )
    tampered = _evaluate(
        replace(status, repository="attacker/repository"),
        provenance,
    )
    inconsistent = _evaluate(
        status,
        replace(provenance, content_sha256="d" * 64),
    )

    assert mismatched.release is ReleaseTruth.UNCERTAIN
    assert "revision" in mismatched.reason
    assert tampered.release is ReleaseTruth.UNCERTAIN
    assert "digest" in tampered.reason
    assert inconsistent.release is ReleaseTruth.UNCERTAIN
    assert "content digest" in inconsistent.reason


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("capability", EvidenceKind.GIT_METADATA, "capability"),
        ("freshness", Freshness.IMMUTABLE_REVISION, "freshness"),
        ("observed_at", "not-a-timestamp", "observation time"),
    ],
)
def test_wrong_receipt_contract_is_uncertain(field: str, value: object, reason: str):
    status, provenance = _evidence(_check("engine"), _check("web"))

    assessment = _evaluate(status, replace(provenance, **{field: value}))

    assert assessment.release is ReleaseTruth.UNCERTAIN
    assert reason in assessment.reason


def test_non_full_revision_is_uncertain_even_when_the_receipt_matches():
    status, provenance = _evidence(_check("engine"), _check("web"))
    status = replace(status, revision=REVISION[:12])
    provenance = replace(
        provenance,
        source_revision=status.revision,
        response_sha256=hash_payload(status.payload()),
    )
    provenance = replace(
        provenance,
        content_sha256=hash_payload(
            {
                "request_sha256": provenance.request_sha256,
                "response_sha256": provenance.response_sha256,
            }
        ),
    )

    assessment = _evaluate(status, provenance)

    assert assessment.release is ReleaseTruth.UNCERTAIN
    assert "full lowercase SHA-1" in assessment.reason


def test_policy_is_connector_provider_neutral_and_deterministic():
    status, github = _evidence(_check("engine"), _check("web"))
    _same_status, gitlab = _evidence(
        _check("engine"),
        _check("web"),
        connector_id="gitlab_ci_status",
    )

    first = _evaluate(status, github)
    second = _evaluate(status, github)
    alternate_provider = _evaluate(status, gitlab)

    assert first == second == alternate_provider


def test_release_assessment_cannot_change_a_failed_claim_verdict():
    status, provenance = _evidence(_check("engine"), _check("web"))
    case = Case(id="case-1", mode=Mode.PROSECUTOR, verdict=Verdict.FAILED)

    assessment = _evaluate(status, provenance)
    case.truth.release = assessment.release
    case.truth.release_reason = assessment.reason

    assert assessment.release is ReleaseTruth.SAFE
    assert case.verdict is Verdict.FAILED
    assert case.truth.goal.value == "UNCERTAIN"


@pytest.mark.parametrize(
    "policy",
    [
        lambda: ReleasePolicy((), 3600),
        lambda: ReleasePolicy(("engine", "engine"), 3600),
        lambda: ReleasePolicy((" engine",), 3600),
        lambda: ReleasePolicy(("engine",), 0),
        lambda: ReleasePolicy(("engine",), True),
        lambda: ReleasePolicy(("engine",), 3600, schema_version="release-policy/v2"),
    ],
)
def test_rejects_invalid_policies(policy):
    with pytest.raises(ValueError):
        policy()


def test_rejects_a_naive_evaluation_time():
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_release_policy(
            status=None,
            provenance=None,
            policy=_policy("engine"),
            evaluated_at=datetime(2026, 9, 1),
        )
