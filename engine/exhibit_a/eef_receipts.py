"""Bounded, claim-bound remote connector receipts for EEF v3."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
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
from .connectors.ci_status import CIStatus

RECEIPT_ENTRY = "connector_receipts.json"
RECEIPT_SCHEMA = "connector-receipts/v1"
PAYLOAD_SCHEMA = "ci-status/v1"

_MAX_RECEIPTS = 32
_MAX_RECEIPT_BYTES = 1024 * 1024
_MAX_CHECKS = 250
_FULL_SHA = re.compile(r"[0-9a-f]{40}")
_REPOSITORY = re.compile(r"[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}")
_HEX_32 = re.compile(r"[0-9a-f]{32}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_DOCUMENT_KEYS = {"schema_version", "claim_binding", "receipts"}
_BINDING_KEYS = {
    "claim_type",
    "claim_sha256",
    "repository_origin",
    "repository",
    "revision",
}
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


@dataclass(frozen=True)
class ReceiptBinding:
    claim_type: str
    claim_sha256: str
    repository_origin: str
    repository: str
    revision: str

    def __post_init__(self) -> None:
        if not isinstance(self.claim_type, str) or not self.claim_type.strip():
            raise ValueError("EEF receipt claim type is invalid")
        if not _HEX_64.fullmatch(self.claim_sha256):
            raise ValueError("EEF receipt claim digest is invalid")
        if not _is_origin(self.repository_origin):
            raise ValueError("EEF receipt repository origin is invalid")
        if not _REPOSITORY.fullmatch(self.repository):
            raise ValueError("EEF receipt repository is invalid")
        if not _FULL_SHA.fullmatch(self.revision):
            raise ValueError("EEF receipt revision must be a full lowercase SHA-1")


@dataclass(frozen=True)
class ReceiptArchive:
    content: bytes
    receipts: tuple[dict[str, Any], ...]
    binding: ReceiptBinding

    @property
    def root_sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    @property
    def manifest_metadata(self) -> dict[str, object]:
        return {
            "receipt_schema": RECEIPT_SCHEMA,
            "receipt_count": len(self.receipts),
            "receipt_root_sha256": self.root_sha256,
        }


def claim_binding(
    *,
    claim_type: str,
    claim_content: bytes,
    repository_source: object,
    revision: object,
) -> ReceiptBinding:
    """Derive a receipt binding from the exact archived claim and target source."""
    if not isinstance(repository_source, str):
        raise ValueError("EEF remote receipts require a repository source URL")
    if not isinstance(revision, str):
        raise ValueError("EEF remote receipts require a target revision")
    repository_origin, repository = repository_coordinates(repository_source)
    return ReceiptBinding(
        claim_type=claim_type,
        claim_sha256=hashlib.sha256(claim_content).hexdigest(),
        repository_origin=repository_origin,
        repository=repository,
        revision=revision,
    )


def build_receipt_archive(
    outputs: Sequence[ConnectorOutput[CIStatus]],
    binding: ReceiptBinding,
) -> ReceiptArchive:
    """Serialize remote CI outputs and revalidate the result readers will consume."""
    if isinstance(outputs, (str, bytes)) or not 1 <= len(outputs) <= _MAX_RECEIPTS:
        raise ValueError("EEF v3 requires between 1 and 32 remote connector receipts")
    document = {
        "schema_version": RECEIPT_SCHEMA,
        "claim_binding": asdict(binding),
        "receipts": [_serialize_output(output) for output in outputs],
    }
    content = _canonical(document) + b"\n"
    return validate_receipt_archive(content, binding)


def validate_receipt_archive(content: bytes, binding: ReceiptBinding) -> ReceiptArchive:
    """Validate a signed receipt section without network access or ambient state."""
    if not isinstance(content, bytes) or not 0 < len(content) <= _MAX_RECEIPT_BYTES:
        raise ValueError("EEF connector receipt section exceeds its size limit")
    try:
        document = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("EEF connector receipt section is invalid JSON") from exc
    _validate_json_depth(document)
    if not isinstance(document, dict) or set(document) != _DOCUMENT_KEYS:
        raise ValueError("EEF connector receipt document has an invalid shape")
    if document.get("schema_version") != RECEIPT_SCHEMA:
        raise ValueError("EEF connector receipt schema is unsupported")
    raw_binding = document.get("claim_binding")
    if not isinstance(raw_binding, dict) or set(raw_binding) != _BINDING_KEYS:
        raise ValueError("EEF connector receipt claim binding is invalid")
    if raw_binding != asdict(binding):
        raise ValueError("EEF connector receipts do not match the archived claim")
    raw_receipts = document.get("receipts")
    if not isinstance(raw_receipts, list) or not 1 <= len(raw_receipts) <= _MAX_RECEIPTS:
        raise ValueError("EEF connector receipt count is invalid")

    receipts: list[dict[str, Any]] = []
    evidence_ids: set[str] = set()
    for raw_receipt in raw_receipts:
        receipt = _validate_receipt(raw_receipt, binding)
        evidence_id = receipt["provenance"]["evidence_id"]
        if evidence_id in evidence_ids:
            raise ValueError("EEF connector receipt evidence IDs must be unique")
        evidence_ids.add(evidence_id)
        receipts.append(receipt)
    if content != _canonical(document) + b"\n":
        raise ValueError("EEF connector receipt document is noncanonical")
    return ReceiptArchive(content, tuple(receipts), binding)


def validate_receipt_metadata(
    manifest: Mapping[str, Any],
    predicate: Mapping[str, Any],
    archive: ReceiptArchive,
) -> None:
    """Require signed claim metadata to repeat the receipt section commitment."""
    expected = archive.manifest_metadata
    if any(manifest.get(name) != value for name, value in expected.items()) or any(
        predicate.get(name) != value for name, value in expected.items()
    ):
        raise ValueError("EEF signed connector receipt metadata is inconsistent")


def _serialize_output(output: ConnectorOutput[CIStatus]) -> dict[str, Any]:
    if type(output) is not ConnectorOutput or type(output.payload) is not CIStatus:
        raise TypeError("EEF v3 currently accepts normalized CI connector outputs")
    if type(output.provenance) is not EvidenceProvenance:
        raise TypeError("EEF v3 connector output provenance is invalid")
    return {
        "payload_schema": PAYLOAD_SCHEMA,
        "request": {
            "repository": output.payload.repository,
            "revision": output.payload.revision,
            "source": output.provenance.source,
        },
        "payload": output.payload.payload(),
        "provenance": asdict(output.provenance),
    }


def _validate_receipt(raw: object, binding: ReceiptBinding) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != _RECEIPT_KEYS:
        raise ValueError("EEF connector receipt has an invalid shape")
    if raw.get("payload_schema") != PAYLOAD_SCHEMA:
        raise ValueError("EEF connector receipt payload schema is unsupported")
    request = raw.get("request")
    payload = raw.get("payload")
    provenance = raw.get("provenance")
    _validate_request(request, binding)
    _validate_payload(payload, binding)
    _validate_provenance(provenance, request, payload, binding)
    return raw


def _validate_request(request: object, binding: ReceiptBinding) -> None:
    if not isinstance(request, dict) or set(request) != _REQUEST_KEYS:
        raise ValueError("EEF connector receipt request has an invalid shape")
    if request.get("repository") != binding.repository:
        raise ValueError("EEF connector receipt request repository does not match its claim")
    if request.get("revision") != binding.revision:
        raise ValueError("EEF connector receipt request revision does not match its claim")
    source = request.get("source")
    if (
        not isinstance(source, str)
        or credential_free_source(source) != source
        or not _safe_remote_url(source)
    ):
        raise ValueError("EEF connector receipt request source is unsafe or not credential-free")


def _validate_payload(payload: object, binding: ReceiptBinding) -> None:
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
        raise ValueError("EEF connector receipt payload has an invalid shape")
    if payload.get("repository") != binding.repository:
        raise ValueError("EEF connector receipt repository does not match its claim")
    if payload.get("revision") != binding.revision:
        raise ValueError("EEF connector receipt revision does not match its claim")
    reported_total = payload.get("reported_total")
    if (
        not isinstance(reported_total, int)
        or isinstance(reported_total, bool)
        or reported_total < 0
    ):
        raise ValueError("EEF connector receipt reported total is invalid")
    checks = payload.get("checks")
    if not isinstance(checks, list) or len(checks) > _MAX_CHECKS:
        raise ValueError("EEF connector receipt check list is invalid")
    for check in checks:
        if not isinstance(check, dict) or set(check) != _CHECK_KEYS:
            raise ValueError("EEF connector receipt check has an invalid shape")
        name = check.get("name")
        status = check.get("status")
        conclusion = check.get("conclusion")
        if (
            not isinstance(name, str)
            or not name.strip()
            or name != name.strip()
            or len(name) > 256
            or not isinstance(status, str)
            or not status
            or len(status) > 64
            or (
                conclusion is not None and (not isinstance(conclusion, str) or len(conclusion) > 64)
            )
        ):
            raise ValueError("EEF connector receipt check fields are invalid")
        for field in ("started_at", "completed_at"):
            if check.get(field) is not None and not _is_aware_timestamp(check[field]):
                raise ValueError("EEF connector receipt check timestamp is invalid")


def _validate_provenance(
    provenance: object,
    request: dict[str, Any],
    payload: dict[str, Any],
    binding: ReceiptBinding,
) -> None:
    if not isinstance(provenance, dict) or set(provenance) != _PROVENANCE_KEYS:
        raise ValueError("EEF connector receipt provenance has an invalid shape")
    if not _HEX_32.fullmatch(provenance.get("evidence_id", "")):
        raise ValueError("EEF connector receipt evidence ID is invalid")
    for field in ("connector_id", "connector_version", "description"):
        value = provenance.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 512:
            raise ValueError("EEF connector receipt provenance identity is invalid")
    if provenance.get("capability") != EvidenceKind.CI_STATUS.value:
        raise ValueError("EEF connector receipt capability is invalid")
    if provenance.get("freshness") != Freshness.POINT_IN_TIME.value:
        raise ValueError("EEF connector receipt freshness is invalid")
    if provenance.get("source_revision") != binding.revision:
        raise ValueError("EEF connector receipt source revision does not match its claim")
    source = provenance.get("source")
    if (
        not isinstance(source, str)
        or credential_free_source(source) != source
        or not _safe_remote_url(source)
    ):
        raise ValueError("EEF connector receipt source is unsafe or not credential-free")
    if source != request["source"]:
        raise ValueError("EEF connector receipt source does not match its request")
    _validate_connector_source(
        source,
        connector_id=provenance["connector_id"],
        connector_version=provenance["connector_version"],
        binding=binding,
    )
    if not _is_aware_timestamp(provenance.get("observed_at")):
        raise ValueError("EEF connector receipt observation time is invalid")
    source_updated_at = provenance.get("source_updated_at")
    if source_updated_at is not None and not _is_aware_timestamp(source_updated_at):
        raise ValueError("EEF connector receipt source update time is invalid")
    for field in (
        "request_sha256",
        "response_sha256",
        "artifact_sha256",
        "content_sha256",
    ):
        if not _HEX_64.fullmatch(provenance.get(field, "")):
            raise ValueError("EEF connector receipt digest is invalid")
    expected_request = hash_payload(request)
    if provenance["request_sha256"] != expected_request:
        raise ValueError("EEF connector receipt request digest does not cover its request")
    expected_response = hash_payload(payload)
    if provenance["response_sha256"] != expected_response:
        raise ValueError("EEF connector receipt response digest does not cover its payload")
    expected_content = hash_payload(
        {
            "request_sha256": provenance["request_sha256"],
            "response_sha256": provenance["response_sha256"],
        }
    )
    if provenance["content_sha256"] != expected_content:
        raise ValueError("EEF connector receipt content digest is inconsistent")
    security = provenance.get("security")
    if not isinstance(security, dict) or set(security) != _SECURITY_KEYS:
        raise ValueError("EEF connector receipt security metadata is invalid")
    try:
        validated_security = ConnectorSecurity(**security)
    except (TypeError, ValueError) as exc:
        raise ValueError("EEF connector receipt security metadata is invalid") from exc
    if validated_security.source_access != "read_only":
        raise ValueError("EEF connector receipt was not collected read-only")


def repository_coordinates(source: object) -> tuple[str, str]:
    """Return the canonical origin and owner/name identity for a remote repository."""
    if not isinstance(source, str) or not _safe_remote_url(source):
        raise ValueError("EEF remote receipts require a safe repository source URL")
    parsed = urlsplit(source)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        raise ValueError("EEF remote receipt repository source must identify owner/name")
    name = parts[1].removesuffix(".git")
    repository = f"{parts[0]}/{name}"
    if not _REPOSITORY.fullmatch(repository):
        raise ValueError("EEF remote receipt repository source is invalid")
    return _url_origin(parsed), repository


def _validate_connector_source(
    source: str,
    *,
    connector_id: str,
    connector_version: str,
    binding: ReceiptBinding,
) -> None:
    if (connector_id, connector_version) != ("github_ci_status", "1"):
        raise ValueError("EEF connector receipt source adapter is unsupported")
    parsed = urlsplit(source)
    expected_origin = (
        "https://api.github.com"
        if binding.repository_origin == "https://github.com"
        else binding.repository_origin
    )
    expected_suffix = f"/repos/{binding.repository}"
    path = parsed.path.rstrip("/")
    if _url_origin(parsed) != expected_origin or (
        path != expected_suffix
        if binding.repository_origin == "https://github.com"
        else not path.endswith(expected_suffix)
    ):
        raise ValueError("EEF connector receipt source does not match its repository origin")


def _safe_remote_url(value: str) -> bool:
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
    ):
        return False
    if parsed.scheme == "https":
        return True
    try:
        return ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def _url_origin(parsed) -> str:
    hostname = parsed.hostname.lower()
    host = f"[{hostname}]" if ":" in hostname else hostname
    port = parsed.port
    if port is not None and (parsed.scheme, port) not in {("http", 80), ("https", 443)}:
        host = f"{host}:{port}"
    return f"{parsed.scheme.lower()}://{host}"


def _is_origin(value: object) -> bool:
    if not isinstance(value, str) or not _safe_remote_url(value):
        return False
    parsed = urlsplit(value)
    return value == _url_origin(parsed) and not parsed.path


def _validate_json_depth(value: object, *, maximum: int = 64) -> None:
    pending = [(value, 0)]
    while pending:
        current, depth = pending.pop()
        if depth > maximum:
            raise ValueError("EEF connector receipt JSON exceeds its nesting limit")
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)


def _is_aware_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 64:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
