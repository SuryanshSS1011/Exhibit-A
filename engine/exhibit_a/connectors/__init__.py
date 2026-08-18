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
from .ci_status import CICheckRun, CIStatus, CIStatusConnector, CIStatusRequest
from .git import GitChange, GitMetadata, GitMetadataConnector, GitMetadataRequest
from .local_test import (
    LocalTestConnector,
    LocalTestRequest,
    collect_validated_local_test,
    local_test_digests,
)

__all__ = [
    "CICheckRun",
    "CIStatus",
    "CIStatusConnector",
    "CIStatusRequest",
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
    "collect_validated_local_test",
    "hash_payload",
    "credential_free_source",
    "local_test_digests",
]
