"""Signed release-policy records derived from archived CI connector receipts."""

from __future__ import annotations

import re
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

from .connectors.base import (
    ConnectorOutput,
    ConnectorSecurity,
    EvidenceKind,
    EvidenceProvenance,
    Freshness,
    credential_free_source,
    hash_payload,
)
from .connectors.ci_status import CICheckRun, CIStatus
from .models.case import ReleaseTruth
from .verdict.release_policy import (
    ReleaseAssessment,
    ReleasePolicy,
    evaluate_release_policy,
)

RELEASE_EVIDENCE_SCHEMA = "release-evidence/v1"

_POLICY_KEYS = {"schema_version", "name", "required_checks", "max_age_s"}
_RECORD_KEYS = {"schema_version", "policy", "receipt_evidence_id", "evaluated_at"}
_RECEIPT_KEYS = {"payload_schema", "request", "payload", "provenance"}
_REQUEST_KEYS = {"repository", "revision", "source"}
_PAYLOAD_KEYS = {"repository", "revision", "reported_total", "checks"}
_CHECK_KEYS = {"name", "status", "conclusion", "started_at", "completed_at"}
_PROVENANCE_KEYS = {
    "evidence_id",
    "connector_id",
    "connector_version",
    "capability",
    "source",
    "source_revision",
    "observed_at",
    "source_updated_at",
    "freshness",
    "description",
    "request_sha256",
    "response_sha256",
    "artifact_sha256",
    "content_sha256",
    "security",
}
_SECURITY_KEYS = {"source_access", "network_access", "isolation", "credential_access"}
_POLICY_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_REPOSITORY = re.compile(r"[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}")
_FULL_SHA = re.compile(r"[0-9a-f]{40}")
_HEX_32 = re.compile(r"[0-9a-f]{32}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_MAX_CHECKS = 250


@dataclass(frozen=True)
class NamedReleasePolicy:
    """A local policy name plus the pure, provider-neutral policy contract."""

    name: str
    policy: ReleasePolicy

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.policy.schema_version,
            "name": self.name,
            "required_checks": list(self.policy.required_checks),
            "max_age_s": self.policy.max_age_s,
        }


@dataclass(frozen=True)
class ValidatedReleaseEvidence:
    """One offline-rederived release assessment and its validated inputs."""

    record: dict[str, Any]
    named_policy: NamedReleasePolicy
    output: ConnectorOutput[CIStatus]
    assessment: ReleaseAssessment


def parse_policy_document(value: object) -> NamedReleasePolicy:
    """Parse the exact, versioned local policy document accepted by the workflow."""
    if not isinstance(value, Mapping) or set(value) != _POLICY_KEYS:
        raise ValueError("release policy document has an invalid shape")
    name = value.get("name")
    if not isinstance(name, str) or not _POLICY_NAME.fullmatch(name):
        raise ValueError("release policy name must be a safe 1-64 character slug")
    required_checks = value.get("required_checks")
    if not isinstance(required_checks, list):
        raise ValueError("release policy required_checks must be a list")
    try:
        policy = ReleasePolicy(
            schema_version=value.get("schema_version"),
            required_checks=tuple(required_checks),
            max_age_s=value.get("max_age_s"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"release policy document is invalid: {exc}") from exc
    return NamedReleasePolicy(name=name, policy=policy)


def create_release_record(
    output: ConnectorOutput[CIStatus],
    policy: NamedReleasePolicy,
    *,
    evaluated_at: datetime,
) -> tuple[dict[str, Any], ReleaseAssessment]:
    """Evaluate one collected output and create its result-free signed Case record."""
    if not isinstance(policy, NamedReleasePolicy):
        raise TypeError("release evidence requires a parsed named policy")
    policy = parse_policy_document(policy.to_dict())
    validated_output = _validated_output(output)
    assessment = evaluate_release_policy(
        status=validated_output.payload,
        provenance=validated_output.provenance,
        policy=policy.policy,
        evaluated_at=evaluated_at,
    )
    record = {
        "schema_version": RELEASE_EVIDENCE_SCHEMA,
        "policy": policy.to_dict(),
        "receipt_evidence_id": validated_output.provenance.evidence_id,
        "evaluated_at": assessment.evaluated_at,
    }
    return record, assessment


def validate_release_evidence(
    case: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
) -> ValidatedReleaseEvidence | None:
    """Re-derive signed release truth from one validated archived receipt, offline."""
    if not isinstance(case, Mapping):
        raise TypeError("release evidence Case must be an object")
    truth = case.get("truth")
    if not isinstance(truth, Mapping):
        raise ValueError("release evidence Case truth is invalid")
    record = case.get("release_evidence")
    if record is None:
        if truth.get("release") != ReleaseTruth.NOT_ASSESSED.value:
            raise ValueError("assessed release truth requires a release evidence record")
        return None
    if not isinstance(record, Mapping) or set(record) != _RECORD_KEYS:
        raise ValueError("release evidence record has an invalid shape")
    if record.get("schema_version") != RELEASE_EVIDENCE_SCHEMA:
        raise ValueError("release evidence record schema is unsupported")
    if len(receipts) != 1:
        raise ValueError("release evidence requires exactly one connector receipt")

    named_policy = parse_policy_document(record.get("policy"))
    evaluated_at = _parse_canonical_timestamp(
        record.get("evaluated_at"), "release evidence evaluation time"
    )
    output = connector_output_from_receipt(receipts[0])
    if record.get("receipt_evidence_id") != output.provenance.evidence_id:
        raise ValueError("release evidence record references a different connector receipt")
    assessment = evaluate_release_policy(
        status=output.payload,
        provenance=output.provenance,
        policy=named_policy.policy,
        evaluated_at=evaluated_at,
    )
    if (
        truth.get("release") != assessment.release.value
        or truth.get("release_reason") != assessment.reason
    ):
        raise ValueError("release evidence truth does not match its archived inputs")
    return ValidatedReleaseEvidence(
        record=dict(record),
        named_policy=named_policy,
        output=output,
        assessment=assessment,
    )


def public_release_projection(value: ValidatedReleaseEvidence) -> dict[str, Any]:
    """Return the credential-free allowlist used by public passport projections."""
    if not isinstance(value, ValidatedReleaseEvidence):
        raise TypeError("public release evidence requires a validated assessment")
    required = set(value.named_policy.policy.required_checks)
    checks = [
        {
            "name": check.name,
            "status": check.status,
            "conclusion": check.conclusion,
            "started_at": check.started_at,
            "completed_at": check.completed_at,
        }
        for check in value.output.payload.checks
        if check.name in required
    ]
    provenance = value.output.provenance
    policy = value.named_policy.to_dict()
    policy_sha256 = hashlib.sha256(
        json.dumps(
            policy,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    all_names = [check.name for check in value.output.payload.checks]
    return {
        "schema_version": RELEASE_EVIDENCE_SCHEMA,
        "policy": {**policy, "sha256": policy_sha256},
        "checks": checks,
        "collection": {
            "reported_total": value.output.payload.reported_total,
            "collected_count": len(value.output.payload.checks),
            "omitted_check_count": len(value.output.payload.checks) - len(checks),
            "all_check_names_unique": len(all_names) == len(set(all_names)),
        },
        "freshness": {
            "basis": provenance.freshness.value,
            "observed_at": provenance.observed_at,
            "source_updated_at": provenance.source_updated_at,
            "evaluated_at": value.assessment.evaluated_at,
        },
        "provenance": {
            "request_sha256": provenance.request_sha256,
            "response_sha256": provenance.response_sha256,
            "artifact_sha256": provenance.artifact_sha256,
            "content_sha256": provenance.content_sha256,
        },
        "release": value.assessment.release.value,
        "reason": _public_release_reason(value),
    }


def _public_release_reason(value: ValidatedReleaseEvidence) -> str:
    """Explain the assessment without disclosing names outside the policy allowlist."""
    status = value.output.payload
    provenance = value.output.provenance
    policy = value.named_policy.policy
    evaluated = _parse_timestamp(value.assessment.evaluated_at, "release evaluation time")
    observed = _parse_timestamp(provenance.observed_at, "release observation time")
    observation_age = (evaluated - observed).total_seconds()
    if observation_age < 0:
        return "CI observation time is in the future"
    if observation_age > policy.max_age_s:
        return "CI status observation is older than the policy allows"
    if status.reported_total != len(status.checks):
        return "CI status observation is incomplete"

    names = [check.name for check in status.checks]
    if len(names) != len(set(names)):
        return "CI status contains duplicate check names"
    checks = {check.name: check for check in status.checks}
    missing = [name for name in policy.required_checks if name not in checks]
    if missing:
        return f"required CI checks are missing: {', '.join(missing)}"

    failures: list[str] = []
    indeterminate: list[str] = []
    failure_conclusions = {
        "failure",
        "cancelled",
        "timed_out",
        "action_required",
        "startup_failure",
    }
    for name in policy.required_checks:
        check = checks[name]
        timing_issue = _public_completion_issue(
            check.completed_at,
            status=check.status,
            observed=observed,
            evaluated=evaluated,
            max_age_s=policy.max_age_s,
        )
        if timing_issue is not None:
            indeterminate.append(f"{name} ({timing_issue})")
        elif check.status != "completed":
            indeterminate.append(f"{name} ({check.status})")
        elif check.conclusion == "success":
            continue
        elif check.conclusion in failure_conclusions:
            failures.append(f"{name} ({check.conclusion})")
        else:
            indeterminate.append(f"{name} ({check.conclusion or 'no conclusion'})")
    if failures:
        return f"required CI checks failed: {', '.join(failures)}"
    if indeterminate:
        return f"required CI checks are not conclusively successful: {', '.join(indeterminate)}"
    return f"all required CI checks completed successfully: {', '.join(policy.required_checks)}"


def _public_completion_issue(
    completed_at: str | None,
    *,
    status: str,
    observed: datetime,
    evaluated: datetime,
    max_age_s: int,
) -> str | None:
    if status != "completed":
        return None
    try:
        completed = _parse_timestamp(completed_at, "release check completion time")
    except ValueError:
        return "invalid completion time"
    if completed > observed or completed > evaluated:
        return "completion time is in the future"
    if (evaluated - completed).total_seconds() > max_age_s:
        return "completion is stale"
    return None


def connector_output_from_receipt(receipt: Mapping[str, Any]) -> ConnectorOutput[CIStatus]:
    """Reconstruct typed CI evidence from an exact archived receipt dictionary."""
    if not isinstance(receipt, Mapping) or set(receipt) != _RECEIPT_KEYS:
        raise ValueError("release evidence receipt has an invalid shape")
    if receipt.get("payload_schema") != "ci-status/v1":
        raise ValueError("release evidence receipt payload schema is unsupported")
    request = receipt.get("request")
    payload = receipt.get("payload")
    provenance = receipt.get("provenance")
    if not isinstance(request, Mapping) or set(request) != _REQUEST_KEYS:
        raise ValueError("release evidence receipt request is invalid")
    if not isinstance(payload, Mapping) or set(payload) != _PAYLOAD_KEYS:
        raise ValueError("release evidence receipt payload is invalid")
    if not isinstance(provenance, Mapping) or set(provenance) != _PROVENANCE_KEYS:
        raise ValueError("release evidence receipt provenance is invalid")

    repository = payload.get("repository")
    revision = payload.get("revision")
    if (
        not isinstance(repository, str)
        or not _REPOSITORY.fullmatch(repository)
        or request.get("repository") != repository
    ):
        raise ValueError("release evidence receipt repository is invalid")
    if (
        not isinstance(revision, str)
        or not _FULL_SHA.fullmatch(revision)
        or request.get("revision") != revision
        or provenance.get("source_revision") != revision
    ):
        raise ValueError("release evidence receipt revision is invalid")
    source = request.get("source")
    if (
        not isinstance(source, str)
        or not _safe_remote_source(source)
        or provenance.get("source") != source
    ):
        raise ValueError("release evidence receipt source is invalid")

    reported_total = payload.get("reported_total")
    raw_checks = payload.get("checks")
    if (
        not isinstance(reported_total, int)
        or isinstance(reported_total, bool)
        or reported_total < 0
        or not isinstance(raw_checks, list)
        or len(raw_checks) > _MAX_CHECKS
    ):
        raise ValueError("release evidence receipt CI summary is invalid")
    checks = tuple(_parse_check(check) for check in raw_checks)
    status = CIStatus(repository, revision, reported_total, checks)

    if not _HEX_32.fullmatch(str(provenance.get("evidence_id", ""))):
        raise ValueError("release evidence receipt evidence ID is invalid")
    for field in ("connector_id", "connector_version", "description"):
        field_value = provenance.get(field)
        if not isinstance(field_value, str) or not field_value.strip() or len(field_value) > 512:
            raise ValueError("release evidence receipt provenance identity is invalid")
    if provenance.get("capability") != EvidenceKind.CI_STATUS.value:
        raise ValueError("release evidence receipt capability is invalid")
    if provenance.get("freshness") != Freshness.POINT_IN_TIME.value:
        raise ValueError("release evidence receipt freshness is invalid")
    observed_at = _canonical_timestamp(
        provenance.get("observed_at"), "release evidence receipt observation time"
    )
    source_updated_at = provenance.get("source_updated_at")
    if source_updated_at is not None:
        _parse_timestamp(source_updated_at, "release evidence receipt source update time")
    for field in (
        "request_sha256",
        "response_sha256",
        "artifact_sha256",
        "content_sha256",
    ):
        if not _HEX_64.fullmatch(str(provenance.get(field, ""))):
            raise ValueError("release evidence receipt digest is invalid")
    if provenance["request_sha256"] != hash_payload(dict(request)):
        raise ValueError("release evidence request digest does not match its request")
    if provenance["response_sha256"] != hash_payload(dict(payload)):
        raise ValueError("release evidence response digest does not match its payload")
    if provenance["content_sha256"] != hash_payload(
        {
            "request_sha256": provenance["request_sha256"],
            "response_sha256": provenance["response_sha256"],
        }
    ):
        raise ValueError("release evidence content digest is inconsistent")

    security = provenance.get("security")
    if not isinstance(security, Mapping) or set(security) != _SECURITY_KEYS:
        raise ValueError("release evidence receipt security is invalid")
    try:
        validated_security = ConnectorSecurity(**dict(security))
    except (TypeError, ValueError) as exc:
        raise ValueError("release evidence receipt security is invalid") from exc
    if validated_security.source_access != "read_only":
        raise ValueError("release evidence receipt was not collected read-only")

    typed_provenance = EvidenceProvenance(
        evidence_id=provenance["evidence_id"],
        connector_id=provenance["connector_id"],
        connector_version=provenance["connector_version"],
        capability=EvidenceKind.CI_STATUS,
        source=source,
        source_revision=revision,
        observed_at=observed_at,
        source_updated_at=source_updated_at,
        freshness=Freshness.POINT_IN_TIME,
        description=provenance["description"],
        request_sha256=provenance["request_sha256"],
        response_sha256=provenance["response_sha256"],
        artifact_sha256=provenance["artifact_sha256"],
        content_sha256=provenance["content_sha256"],
        security=validated_security,
    )
    return ConnectorOutput(payload=status, provenance=typed_provenance)


def _validated_output(output: ConnectorOutput[CIStatus]) -> ConnectorOutput[CIStatus]:
    if type(output) is not ConnectorOutput or type(output.payload) is not CIStatus:
        raise TypeError("release evidence requires one normalized CI connector output")
    if type(output.provenance) is not EvidenceProvenance:
        raise TypeError("release evidence connector provenance is invalid")
    receipt = {
        "payload_schema": "ci-status/v1",
        "request": {
            "repository": output.payload.repository,
            "revision": output.payload.revision,
            "source": output.provenance.source,
        },
        "payload": {
            "repository": output.payload.repository,
            "revision": output.payload.revision,
            "reported_total": output.payload.reported_total,
            "checks": [
                {
                    "name": check.name,
                    "status": check.status,
                    "conclusion": check.conclusion,
                    "started_at": check.started_at,
                    "completed_at": check.completed_at,
                }
                for check in output.payload.checks
            ],
        },
        "provenance": {
            "evidence_id": output.provenance.evidence_id,
            "connector_id": output.provenance.connector_id,
            "connector_version": output.provenance.connector_version,
            "capability": output.provenance.capability.value,
            "source": output.provenance.source,
            "source_revision": output.provenance.source_revision,
            "observed_at": output.provenance.observed_at,
            "source_updated_at": output.provenance.source_updated_at,
            "freshness": output.provenance.freshness.value,
            "description": output.provenance.description,
            "request_sha256": output.provenance.request_sha256,
            "response_sha256": output.provenance.response_sha256,
            "artifact_sha256": output.provenance.artifact_sha256,
            "content_sha256": output.provenance.content_sha256,
            "security": {
                "source_access": output.provenance.security.source_access,
                "network_access": output.provenance.security.network_access,
                "isolation": output.provenance.security.isolation,
                "credential_access": output.provenance.security.credential_access,
            },
        },
    }
    return connector_output_from_receipt(receipt)


def _parse_check(value: object) -> CICheckRun:
    if not isinstance(value, Mapping) or set(value) != _CHECK_KEYS:
        raise ValueError("release evidence receipt check is invalid")
    name = value.get("name")
    status = value.get("status")
    conclusion = value.get("conclusion")
    if (
        not isinstance(name, str)
        or not name.strip()
        or name != name.strip()
        or len(name) > 256
        or not isinstance(status, str)
        or not status
        or len(status) > 64
        or conclusion is not None
        and (not isinstance(conclusion, str) or len(conclusion) > 64)
    ):
        raise ValueError("release evidence receipt check fields are invalid")
    started_at = value.get("started_at")
    completed_at = value.get("completed_at")
    if started_at is not None:
        _parse_timestamp(started_at, "release evidence receipt check start time")
    if completed_at is not None:
        _parse_timestamp(completed_at, "release evidence receipt check completion time")
    return CICheckRun(name, status, conclusion, started_at, completed_at)


def _safe_remote_source(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        parsed.port
    except (TypeError, ValueError):
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or credential_free_source(value) != value
    ):
        return False
    if parsed.scheme == "https":
        return True
    try:
        return ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def _parse_canonical_timestamp(value: object, label: str) -> datetime:
    parsed = _parse_timestamp(value, label)
    if value != parsed.isoformat():
        raise ValueError(f"{label} must use canonical UTC ISO 8601 form")
    return parsed


def _canonical_timestamp(value: object, label: str) -> str:
    return _parse_timestamp(value, label).isoformat()


def _parse_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)
