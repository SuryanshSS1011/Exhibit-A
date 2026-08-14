"""Connector adapter for the existing executor-backed test evidence source."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from ..executor.base import ExecOutcome, ExecSpec, Executor, RepoState
from .base import (
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
            version="1",
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


def local_test_digests(request: LocalTestRequest, outcome: ExecOutcome) -> dict[str, str]:
    """Bind test input/output without publishing rejected-attempt content."""
    request_sha256 = hash_payload(
        {
            "command": request.spec.command,
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
