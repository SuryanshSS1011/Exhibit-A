"""Shared contract for read-only evidence sources."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Generic, Mapping, Protocol, TypeVar

RequestT = TypeVar("RequestT", contravariant=True)
PayloadT = TypeVar("PayloadT", covariant=True)


class EvidenceKind(str, Enum):
    TEST_EXECUTION = "test_execution"


class Freshness(str, Enum):
    POINT_IN_TIME = "point_in_time"
    CACHED = "cached"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ConnectorSecurity:
    source_access: str
    network_access: str
    isolation: str
    credential_access: str = "none"

    def __post_init__(self) -> None:
        allowed = {
            "source_access": {"read_only", "disposable_copy", "unknown"},
            "network_access": {"disabled", "host_unrestricted", "unknown"},
            "isolation": {"container", "host_subprocess", "unknown"},
            "credential_access": {"none", "ambient_host", "unknown"},
        }
        for field_name, values in allowed.items():
            if getattr(self, field_name) not in values:
                raise ValueError(f"unsupported connector security {field_name}")


@dataclass(frozen=True)
class ConnectorDescriptor:
    id: str
    version: str
    capabilities: tuple[EvidenceKind, ...]
    freshness_basis: Freshness
    security: ConnectorSecurity

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.version.strip() or not self.capabilities:
            raise ValueError("connector descriptor identity and capabilities are required")


@dataclass(frozen=True)
class EvidenceProvenance:
    evidence_id: str
    connector_id: str
    connector_version: str
    capability: EvidenceKind
    source: str
    source_revision: str | None
    observed_at: str
    source_updated_at: str | None
    freshness: Freshness
    description: str
    request_sha256: str
    response_sha256: str
    artifact_sha256: str
    content_sha256: str
    security: ConnectorSecurity

    def __post_init__(self) -> None:
        if len(self.evidence_id) != 32 or any(
            character not in "0123456789abcdef" for character in self.evidence_id
        ):
            raise ValueError("connector evidence_id must be 128-bit lowercase hex")
        for label, digest in (
            ("request", self.request_sha256),
            ("response", self.response_sha256),
            ("artifact", self.artifact_sha256),
            ("content", self.content_sha256),
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError(f"connector {label}_sha256 must be lowercase SHA-256 hex")


@dataclass(frozen=True)
class ConnectorOutput(Generic[PayloadT]):
    payload: PayloadT
    provenance: EvidenceProvenance


class Connector(Protocol, Generic[RequestT, PayloadT]):
    """A typed, read-only fact collector with no verdict authority."""

    descriptor: ConnectorDescriptor

    def collect(self, request: RequestT) -> ConnectorOutput[PayloadT]: ...


def hash_payload(payload: Mapping[str, object]) -> str:
    """Hash one normalized connector payload for tamper-evident provenance."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
