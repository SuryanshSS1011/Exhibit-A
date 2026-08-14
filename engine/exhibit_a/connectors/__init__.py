"""Read-only, typed evidence-source connectors."""

from .base import (
    Connector,
    ConnectorDescriptor,
    ConnectorOutput,
    ConnectorSecurity,
    EvidenceKind,
    EvidenceProvenance,
    Freshness,
    hash_payload,
)
from .local_test import LocalTestConnector, LocalTestRequest, local_test_digests

__all__ = [
    "Connector",
    "ConnectorDescriptor",
    "ConnectorOutput",
    "ConnectorSecurity",
    "EvidenceKind",
    "EvidenceProvenance",
    "Freshness",
    "LocalTestConnector",
    "LocalTestRequest",
    "hash_payload",
    "local_test_digests",
]
