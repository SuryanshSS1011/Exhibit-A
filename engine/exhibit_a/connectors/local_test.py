"""Connector adapter for the existing executor-backed test evidence source."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from ..executor.base import ExecOutcome, ExecSpec, Executor, RepoState
from .base import (
    Connector,
    ConnectorDescriptor,
    ConnectorOutput,
    ConnectorSecurity,
    EvidenceKind,
    EvidenceProvenance,
    Freshness,
    credential_free_source,
    hash_payload,
)


@dataclass(frozen=True)
class LocalTestRequest:
    repo: RepoState
    spec: ExecSpec


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LocalTestConnector:
    """Collect raw execution evidence without interpreting it as a verdict."""

    def __init__(self, executor: Executor, *, clock: Callable[[], datetime] = _utc_now):
        self._executor = executor
        self._clock = clock
        self.descriptor = ConnectorDescriptor(
            id="local_test_runner",
            version="2",
            capabilities=(EvidenceKind.TEST_EXECUTION,),
            freshness_basis=Freshness.POINT_IN_TIME,
            security=ConnectorSecurity(
                source_access=executor.source_access,
                network_access=executor.network_access,
                isolation=executor.isolation,
                credential_access=executor.credential_access,
            ),
        )

    def collect(self, request: LocalTestRequest) -> ConnectorOutput[ExecOutcome]:
        if request.spec.network:
            raise ValueError("test evidence connector does not permit network-enabled requests")
        observed_at = self._clock()
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("connector clock must return a timezone-aware datetime")
        outcome = self._executor.run(request.repo, request.spec)
        digests = local_test_digests(request, outcome)
        provenance = EvidenceProvenance(
            evidence_id=uuid.uuid4().hex,
            connector_id=self.descriptor.id,
            connector_version=self.descriptor.version,
            capability=EvidenceKind.TEST_EXECUTION,
            source=credential_free_source(request.repo.source),
            source_revision=request.repo.commit,
            observed_at=observed_at.astimezone(timezone.utc).isoformat(),
            source_updated_at=None,
            freshness=self.descriptor.freshness_basis,
            description=f"Executed the configured test against the {request.repo.label} code state",
            request_sha256=digests["request_sha256"],
            response_sha256=digests["response_sha256"],
            artifact_sha256=digests["artifact_sha256"],
            content_sha256=digests["content_sha256"],
            security=self.descriptor.security,
        )
        return ConnectorOutput(payload=outcome, provenance=provenance)


def collect_validated_local_test(
    connector: Connector[LocalTestRequest, ExecOutcome], request: LocalTestRequest
) -> ConnectorOutput[ExecOutcome]:
    """Collect one execution and fail closed unless its receipt covers the result."""
    if request.spec.network:
        raise ValueError("test evidence connector does not permit network-enabled requests")
    raw_descriptor = connector.descriptor
    if type(raw_descriptor) is not ConnectorDescriptor:
        raise TypeError("test connector returned an invalid descriptor")
    if type(raw_descriptor.security) is not ConnectorSecurity:
        raise TypeError("test connector returned invalid descriptor security")
    descriptor = ConnectorDescriptor(
        id=raw_descriptor.id,
        version=raw_descriptor.version,
        capabilities=tuple(raw_descriptor.capabilities),
        freshness_basis=raw_descriptor.freshness_basis,
        security=ConnectorSecurity(
            source_access=raw_descriptor.security.source_access,
            network_access=raw_descriptor.security.network_access,
            isolation=raw_descriptor.security.isolation,
            credential_access=raw_descriptor.security.credential_access,
        ),
    )
    collected = connector.collect(request)
    if connector.descriptor != descriptor:
        raise ValueError("test connector descriptor changed during collection")
    if not isinstance(collected, ConnectorOutput):
        raise TypeError("test connector returned an invalid output envelope")
    if type(collected.payload) is not ExecOutcome:
        raise TypeError("test connector returned a non-ExecOutcome payload")
    if type(collected.provenance) is not EvidenceProvenance:
        raise TypeError("test connector returned invalid provenance")
    if type(collected.provenance.security) is not ConnectorSecurity:
        raise TypeError("test connector returned invalid provenance security")

    raw = collected.payload
    values = (
        raw.exit_code,
        raw.stdout,
        raw.stderr,
        raw.timed_out,
        raw.duration_s,
    )
    exit_code, stdout, stderr, timed_out, duration_s = values
    if (
        not isinstance(exit_code, int)
        or isinstance(exit_code, bool)
        or not isinstance(stdout, str)
        or not isinstance(stderr, str)
        or not isinstance(timed_out, bool)
        or isinstance(duration_s, bool)
        or not isinstance(duration_s, (int, float))
        or not math.isfinite(duration_s)
        or duration_s < 0
    ):
        raise ValueError("test connector returned an invalid ExecOutcome")
    outcome = ExecOutcome(exit_code, stdout, stderr, timed_out, duration_s)
    raw_provenance = collected.provenance
    provenance = EvidenceProvenance(
        evidence_id=raw_provenance.evidence_id,
        connector_id=raw_provenance.connector_id,
        connector_version=raw_provenance.connector_version,
        capability=raw_provenance.capability,
        source=raw_provenance.source,
        source_revision=raw_provenance.source_revision,
        observed_at=raw_provenance.observed_at,
        source_updated_at=raw_provenance.source_updated_at,
        freshness=raw_provenance.freshness,
        description=raw_provenance.description,
        request_sha256=raw_provenance.request_sha256,
        response_sha256=raw_provenance.response_sha256,
        artifact_sha256=raw_provenance.artifact_sha256,
        content_sha256=raw_provenance.content_sha256,
        security=ConnectorSecurity(
            source_access=raw_provenance.security.source_access,
            network_access=raw_provenance.security.network_access,
            isolation=raw_provenance.security.isolation,
            credential_access=raw_provenance.security.credential_access,
        ),
    )
    if (
        EvidenceKind.TEST_EXECUTION not in descriptor.capabilities
        or provenance.connector_id != descriptor.id
        or provenance.connector_version != descriptor.version
        or provenance.capability is not EvidenceKind.TEST_EXECUTION
        or provenance.freshness is not descriptor.freshness_basis
        or provenance.security != descriptor.security
    ):
        raise ValueError("test connector provenance does not match its descriptor")
    expected_description = (
        f"Executed the configured test against the {request.repo.label} code state"
    )
    if (
        provenance.source != credential_free_source(request.repo.source)
        or provenance.source_revision != request.repo.commit
        or provenance.source_updated_at is not None
        or provenance.description != expected_description
    ):
        raise ValueError("test connector provenance does not match its request")
    try:
        observed_at = datetime.fromisoformat(provenance.observed_at)
    except (TypeError, ValueError) as exc:
        raise ValueError("test connector observed_at is invalid") from exc
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("test connector observed_at must be timezone-aware")
    expected = local_test_digests(request, outcome)
    if any(getattr(provenance, key) != value for key, value in expected.items()):
        raise ValueError("test connector provenance does not cover its request and response")
    return ConnectorOutput(payload=outcome, provenance=provenance)


def local_test_digests(request: LocalTestRequest, outcome: ExecOutcome) -> dict[str, str]:
    """Bind test input/output without publishing rejected-attempt content."""
    request_sha256 = hash_payload(
        {
            "command": request.spec.command,
            "image": request.spec.image,
            "network": request.spec.network,
            "repo_revision": request.repo.commit,
            "state": request.repo.label,
            "test_code": request.spec.test_code,
            "test_path": request.spec.test_path,
            "timeout_s": request.spec.timeout_s,
        }
    )
    response_sha256 = hash_payload(
        {
            "duration_s": outcome.duration_s,
            "exit_code": outcome.exit_code,
            "stderr": outcome.stderr,
            "stdout": outcome.stdout,
            "timed_out": outcome.timed_out,
        }
    )
    artifact_sha256 = hash_payload(
        {"test_code": request.spec.test_code, "test_path": request.spec.test_path}
    )
    content_sha256 = hash_payload(
        {"request_sha256": request_sha256, "response_sha256": response_sha256}
    )
    return {
        "request_sha256": request_sha256,
        "response_sha256": response_sha256,
        "artifact_sha256": artifact_sha256,
        "content_sha256": content_sha256,
    }
