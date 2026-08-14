"""Read-only, typed evidence-source connectors."""

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
from .git import GitChange, GitMetadata, GitMetadataConnector, GitMetadataRequest
from .local_test import LocalTestConnector, LocalTestRequest, local_test_digests

__all__ = [
    "Connector",
    "ConnectorDescriptor",
    "ConnectorOutput",
    "ConnectorSecurity",
    "EvidenceKind",
    "EvidenceProvenance",
    "Freshness",
    "GitChange",
    "GitMetadata",
    "GitMetadataConnector",
    "GitMetadataRequest",
    "LocalTestConnector",
    "LocalTestRequest",
    "hash_payload",
    "credential_free_source",
    "local_test_digests",
]
