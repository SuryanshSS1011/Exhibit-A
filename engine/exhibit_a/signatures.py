"""Strict DSSE/Ed25519 support for publicly verifiable Exhibit A artifacts."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


EEF_PROFILE = "eef-ed25519-v1"
PASSPORT_PROFILE = "passport-ed25519-v1"
EEF_PAYLOAD_TYPE = "application/vnd.in-toto+json"
PASSPORT_PAYLOAD_TYPE = "application/vnd.exhibit-a.passport.v3+json"
EEF_PREDICATE_TYPE = "https://exhibit-a.dev/eef/v4"
TRUST_ROOT_SCHEMA = "exhibit-a-trust-root/v1"
TRUST_ANCHOR_SCHEMA = "exhibit-a-trust-anchor/v1"
_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")
_KEY_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MAX_DOCUMENT_BYTES = 1024 * 1024
_MAX_DEPTH = 64
_MAX_SIGNATURES = 16
_MAX_KEYS = 64


@dataclass(frozen=True)
class VerifiedIdentity:
    root_id: str
    root_version: int
    root_sha256: str
    policy_id: str
    publisher_id: str
    verified_key_ids: tuple[str, ...]


def canonical_json(value: object) -> bytes:
    """Encode the constrained Exhibit A canonical JSON profile."""
    _validate_json_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def key_id(public_key: bytes) -> str:
    """Return the EEF key ID for one raw RFC 8032 Ed25519 public key."""
    if len(public_key) != 32:
        raise ValueError("Ed25519 public key must contain exactly 32 bytes")
    return "sha256:" + hashlib.sha256(_SPKI_PREFIX + public_key).hexdigest()


def public_key_from_seed(private_key_seed: bytes) -> bytes:
    """Derive a raw public key through the optional cryptography backend."""
    if len(private_key_seed) != 32:
        raise ValueError("Ed25519 private key seed must contain exactly 32 bytes")
    Ed25519PrivateKey, _, Encoding, PublicFormat, _ = _cryptography()
    private_key = Ed25519PrivateKey.from_private_bytes(private_key_seed)
    return private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def policy_publisher(trust_root: bytes, policy_id: str, *, purpose: str) -> str:
    """Return the publisher bound to one validated signing policy."""
    root = _parse_root(trust_root)
    policy = _policy_by_id(root, policy_id)
    if policy.get("purpose") != purpose:
        raise ValueError("trusted policy purpose does not match the requested artifact")
    return str(policy["publisherId"])


def sign_envelope(
    payload: Mapping[str, Any],
    *,
    payload_type: str,
    private_key_seed: bytes,
    trust_root: bytes,
    policy_id: str,
    purpose: str,
) -> dict[str, object]:
    """Create a canonical DSSE envelope after authorizing the signing key."""
    root = _parse_root(trust_root)
    policy = _policy_by_id(root, policy_id)
    _validate_policy_for_payload(policy, payload, payload_type=payload_type, purpose=purpose)
    public_key = public_key_from_seed(private_key_seed)
    signer_key_id = key_id(public_key)
    authorized = _authorized_keys(root, policy)
    if signer_key_id not in authorized:
        raise ValueError("Ed25519 signing key is not authorized by the selected policy")
    encoded_payload = canonical_json(payload)
    Ed25519PrivateKey, _, _, _, _ = _cryptography()
    signature = Ed25519PrivateKey.from_private_bytes(private_key_seed).sign(
        _pae(payload_type.encode("utf-8"), encoded_payload)
    )
    return {
        "payload": base64.b64encode(encoded_payload).decode("ascii"),
        "payloadType": payload_type,
        "signatures": [
            {
                "keyid": signer_key_id,
                "sig": base64.b64encode(signature).decode("ascii"),
            }
        ],
    }


def verify_envelope(
    envelope_content: bytes,
    *,
    trust_root: bytes,
    trust_anchor: bytes,
    purpose: str,
    evaluated_at: datetime | None = None,
) -> tuple[dict[str, Any], VerifiedIdentity]:
    """Verify one DSSE envelope under an externally anchored policy."""
    envelope = parse_json(envelope_content, label="DSSE envelope")
    root = _parse_root(trust_root)
    anchor = _parse_anchor(trust_anchor)
    policy = _anchored_policy(root, anchor, trust_root, purpose=purpose, evaluated_at=evaluated_at)

    payload_type = envelope.get("payloadType")
    payload_encoded = envelope.get("payload")
    signatures = envelope.get("signatures")
    if not isinstance(payload_type, str) or payload_type != policy["payloadType"]:
        raise ValueError("DSSE payload type does not match the trusted policy")
    payload = _decode_dsse_base64(payload_encoded, "DSSE payload")
    if not isinstance(signatures, list) or not 1 <= len(signatures) <= _MAX_SIGNATURES:
        raise ValueError("DSSE signatures are invalid")

    authorized = _authorized_keys(root, policy)
    message = _pae(payload_type.encode("utf-8"), payload)
    seen_hints: set[str] = set()
    verified: set[str] = set()
    _, Ed25519PublicKey, _, _, InvalidSignature = _cryptography()
    for record in signatures:
        if not isinstance(record, dict):
            raise ValueError("DSSE signature record is invalid")
        hint = record.get("keyid")
        if not isinstance(hint, str) or not _KEY_ID.fullmatch(hint) or hint in seen_hints:
            raise ValueError("DSSE signature key ID is invalid")
        seen_hints.add(hint)
        signature = _decode_dsse_base64(record.get("sig"), "DSSE signature")
        if len(signature) != 64:
            raise ValueError("Ed25519 signature must contain exactly 64 bytes")
        candidates = sorted(authorized, key=lambda candidate: candidate != hint)
        for candidate in candidates:
            try:
                Ed25519PublicKey.from_public_bytes(authorized[candidate]).verify(signature, message)
            except InvalidSignature:
                continue
            verified.add(candidate)
            break

    threshold = policy["threshold"]
    if not isinstance(threshold, int) or isinstance(threshold, bool):
        raise ValueError("trust policy threshold is invalid")
    if len(verified) < threshold:
        raise ValueError("DSSE signature threshold was not satisfied")

    parsed_payload = parse_json(payload, label="verified DSSE payload")
    if canonical_json(parsed_payload) != payload:
        raise ValueError("verified DSSE payload is not canonical JSON")
    _validate_policy_for_payload(
        policy,
        parsed_payload,
        payload_type=payload_type,
        purpose=purpose,
    )
    return parsed_payload, VerifiedIdentity(
        root_id=str(root["rootId"]),
        root_version=int(root["rootVersion"]),
        root_sha256=hashlib.sha256(trust_root).hexdigest(),
        policy_id=str(policy["policyId"]),
        publisher_id=str(policy["publisherId"]),
        verified_key_ids=tuple(sorted(verified)),
    )


def parse_json(content: bytes, *, label: str) -> dict[str, Any]:
    """Parse a bounded JSON object while rejecting duplicate keys and non-integers."""
    if not isinstance(content, bytes) or len(content) > _MAX_DOCUMENT_BYTES:
        raise ValueError(f"{label} exceeds the size limit")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in pairs:
            if name in result:
                raise ValueError(f"{label} contains a duplicate JSON key")
            result[name] = value
        return result

    def reject_float(_: str) -> float:
        raise ValueError(f"{label} contains a floating-point value")

    def reject_constant(_: str) -> float:
        raise ValueError(f"{label} contains a non-finite value")

    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_float=reject_float,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f"{label} is invalid JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    _validate_json_value(value)
    return value


def _parse_root(content: bytes) -> dict[str, Any]:
    root = parse_json(content, label="trust root")
    if set(root) != {
        "expiresAt",
        "keys",
        "policies",
        "rootId",
        "rootVersion",
        "schemaVersion",
    } or root.get("schemaVersion") != TRUST_ROOT_SCHEMA:
        raise ValueError("trust root schema is unsupported")
    root_id = root.get("rootId")
    version = root.get("rootVersion")
    keys = root.get("keys")
    policies = root.get("policies")
    if not isinstance(root_id, str) or not root_id.startswith("https://"):
        raise ValueError("trust root ID is invalid")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError("trust root version is invalid")
    if not isinstance(keys, list) or not 1 <= len(keys) <= _MAX_KEYS:
        raise ValueError("trust root keys are invalid")
    if not isinstance(policies, list) or not 1 <= len(policies) <= _MAX_KEYS:
        raise ValueError("trust root policies are invalid")

    key_ids: set[str] = set()
    public_keys: set[bytes] = set()
    for record in keys:
        if not isinstance(record, dict) or set(record) != {
            "algorithm",
            "keyId",
            "label",
            "publicKey",
            "status",
        }:
            raise ValueError("trust root key record is invalid")
        if record.get("algorithm") != "ed25519" or record.get("status") not in {
            "active",
            "revoked",
        }:
            raise ValueError("trust root key algorithm or status is invalid")
        public = record.get("publicKey")
        if not isinstance(public, dict) or set(public) != {"encoding", "value"}:
            raise ValueError("trust root public key record is invalid")
        if public.get("encoding") != "raw-base64":
            raise ValueError("trust root public key encoding is unsupported")
        raw = _decode_standard_base64(public.get("value"), "trust root public key")
        claimed_id = record.get("keyId")
        if len(raw) != 32 or claimed_id != key_id(raw):
            raise ValueError("trust root public key ID is invalid")
        if claimed_id in key_ids or raw in public_keys:
            raise ValueError("trust root contains a duplicate key")
        key_ids.add(str(claimed_id))
        public_keys.add(raw)

    policy_ids: set[str] = set()
    selectors: set[tuple[object, ...]] = set()
    for policy in policies:
        _validate_policy(policy, key_ids)
        policy_id = str(policy["policyId"])
        selector = tuple(
            policy[field]
            for field in (
                "profile",
                "purpose",
                "payloadType",
                "predicateType",
                "publisherId",
            )
        )
        if policy_id in policy_ids or selector in selectors:
            raise ValueError("trust root contains a duplicate policy")
        policy_ids.add(policy_id)
        selectors.add(selector)
    _parse_instant(root.get("expiresAt"), "trust root expiry")
    return root


def _validate_policy(policy: object, key_ids: set[str]) -> None:
    if not isinstance(policy, dict) or set(policy) != {
        "authorizedKeyIds",
        "payloadType",
        "policyId",
        "predicateType",
        "profile",
        "publisherId",
        "purpose",
        "threshold",
    }:
        raise ValueError("trust root policy is invalid")
    policy_id = policy.get("policyId")
    publisher_id = policy.get("publisherId")
    purpose = policy.get("purpose")
    profile = policy.get("profile")
    payload_type = policy.get("payloadType")
    predicate_type = policy.get("predicateType")
    authorized = policy.get("authorizedKeyIds")
    threshold = policy.get("threshold")
    if not isinstance(policy_id, str) or not policy_id:
        raise ValueError("trust policy ID is invalid")
    if not isinstance(publisher_id, str) or not publisher_id.startswith("https://"):
        raise ValueError("trust policy publisher ID is invalid")
    expected = {
        "eef": (EEF_PROFILE, EEF_PAYLOAD_TYPE, EEF_PREDICATE_TYPE),
        "passport": (PASSPORT_PROFILE, PASSPORT_PAYLOAD_TYPE, None),
    }.get(purpose)
    if expected is None or (profile, payload_type, predicate_type) != expected:
        raise ValueError("trust policy profile is unsupported")
    if (
        not isinstance(authorized, list)
        or not authorized
        or any(not isinstance(item, str) or item not in key_ids for item in authorized)
        or len(set(authorized)) != len(authorized)
    ):
        raise ValueError("trust policy authorized keys are invalid")
    if (
        not isinstance(threshold, int)
        or isinstance(threshold, bool)
        or not 1 <= threshold <= len(authorized)
    ):
        raise ValueError("trust policy threshold is invalid")


def _parse_anchor(content: bytes) -> dict[str, Any]:
    anchor = parse_json(content, label="trust anchor")
    required = {
        "expectedPolicyIds",
        "minimumRootVersion",
        "pinnedRootSha256",
        "provisionedAt",
        "rootId",
        "schemaVersion",
    }
    allowed = required | {"testOnly"}
    if not required <= set(anchor) <= allowed or anchor.get("schemaVersion") != TRUST_ANCHOR_SCHEMA:
        raise ValueError("trust anchor schema is unsupported")
    if not isinstance(anchor.get("rootId"), str):
        raise ValueError("trust anchor root ID is invalid")
    minimum = anchor.get("minimumRootVersion")
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1:
        raise ValueError("trust anchor minimum version is invalid")
    digest = anchor.get("pinnedRootSha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("trust anchor root digest is invalid")
    policies = anchor.get("expectedPolicyIds")
    if (
        not isinstance(policies, dict)
        or not policies
        or any(
            purpose not in {"eef", "passport"} or not isinstance(policy_id, str) or not policy_id
            for purpose, policy_id in policies.items()
        )
    ):
        raise ValueError("trust anchor policy IDs are invalid")
    _parse_instant(anchor.get("provisionedAt"), "trust anchor provision time")
    return anchor


def _anchored_policy(
    root: dict[str, Any],
    anchor: dict[str, Any],
    root_content: bytes,
    *,
    purpose: str,
    evaluated_at: datetime | None,
) -> dict[str, Any]:
    if root["rootId"] != anchor["rootId"]:
        raise ValueError("trust root ID does not match its external anchor")
    if not hashlib.sha256(root_content).hexdigest() == anchor["pinnedRootSha256"]:
        raise ValueError("trust root digest does not match its external anchor")
    if root["rootVersion"] < anchor["minimumRootVersion"]:
        raise ValueError("trust root version is below the rollback floor")
    instant = evaluated_at or datetime.now(timezone.utc)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("trust evaluation instant must include a timezone")
    if instant >= _parse_instant(root["expiresAt"], "trust root expiry"):
        raise ValueError("trust root is expired")
    policy_ids = anchor["expectedPolicyIds"]
    if purpose not in policy_ids:
        raise ValueError("trust anchor does not authorize the requested purpose")
    return _policy_by_id(root, policy_ids[purpose])


def _policy_by_id(root: Mapping[str, Any], policy_id: str) -> dict[str, Any]:
    matches = [policy for policy in root["policies"] if policy["policyId"] == policy_id]
    if len(matches) != 1:
        raise ValueError("trusted policy ID does not select exactly one policy")
    return matches[0]


def _authorized_keys(root: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, bytes]:
    records = {record["keyId"]: record for record in root["keys"]}
    authorized: dict[str, bytes] = {}
    for candidate in policy["authorizedKeyIds"]:
        record = records[candidate]
        if record["status"] != "active":
            continue
        authorized[candidate] = _decode_standard_base64(
            record["publicKey"]["value"], "trust root public key"
        )
    if len(authorized) < policy["threshold"]:
        raise ValueError("trust policy cannot meet its threshold with active keys")
    return authorized


def _validate_policy_for_payload(
    policy: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    payload_type: str,
    purpose: str,
) -> None:
    if policy.get("purpose") != purpose or policy.get("payloadType") != payload_type:
        raise ValueError("payload does not match the selected trust policy")
    if purpose == "eef":
        predicate = payload.get("predicate")
        publisher = predicate.get("publisher") if isinstance(predicate, dict) else None
        if (
            payload.get("_type") != "https://in-toto.io/Statement/v1"
            or payload.get("predicateType") != policy.get("predicateType")
            or not isinstance(predicate, dict)
            or predicate.get("signatureProfile") != policy.get("profile")
            or not isinstance(publisher, dict)
            or publisher.get("id") != policy.get("publisherId")
        ):
            raise ValueError("EEF statement does not match the selected trust policy")
    elif purpose == "passport":
        issuer = payload.get("passportIssuer")
        if (
            payload.get("schemaVersion") != "exhibit-a-passport/v3"
            or payload.get("signatureProfile") != policy.get("profile")
            or not isinstance(issuer, dict)
            or issuer.get("id") != policy.get("publisherId")
        ):
            raise ValueError("passport does not match the selected trust policy")
    else:
        raise ValueError("signature purpose is unsupported")


def _decode_standard_base64(value: object, label: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"{label} is invalid")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(f"{label} is invalid") from error
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError(f"{label} is not canonical Base64")
    return decoded


def _decode_dsse_base64(value: object, label: str) -> bytes:
    if not isinstance(value, str) or any(character.isspace() for character in value):
        raise ValueError(f"{label} is invalid")
    uses_standard = "+" in value or "/" in value
    uses_urlsafe = "-" in value or "_" in value
    if uses_standard and uses_urlsafe:
        raise ValueError(f"{label} uses mixed Base64 alphabets")
    try:
        if uses_urlsafe:
            decoded = base64.b64decode(value, altchars=b"-_", validate=True)
            expected = base64.urlsafe_b64encode(decoded).decode("ascii")
        else:
            decoded = base64.b64decode(value, validate=True)
            expected = base64.b64encode(decoded).decode("ascii")
    except (binascii.Error, ValueError) as error:
        raise ValueError(f"{label} is invalid") from error
    if expected != value:
        raise ValueError(f"{label} is not canonical Base64")
    return decoded


def _parse_instant(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{label} is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{label} must be UTC")
    return parsed


def _validate_json_value(value: object, *, depth: int = 0) -> None:
    if depth > _MAX_DEPTH:
        raise ValueError("JSON value exceeds the nesting limit")
    if value is None or isinstance(value, bool) or isinstance(value, str):
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as error:
                raise ValueError("JSON string contains an invalid Unicode scalar") from error
            if unicodedata.normalize("NFC", value) != value:
                raise ValueError("JSON string is not NFC-normalized")
        return
    if isinstance(value, int):
        if not -(2**53) + 1 <= value <= 2**53 - 1:
            raise ValueError("JSON integer exceeds the interoperable range")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        for name, item in value.items():
            if not isinstance(name, str):
                raise ValueError("JSON object key is not a string")
            _validate_json_value(name, depth=depth + 1)
            _validate_json_value(item, depth=depth + 1)
        return
    raise ValueError("JSON value uses an unsupported type")


def _pae(payload_type: bytes, payload: bytes) -> bytes:
    return b" ".join(
        (
            b"DSSEv1",
            str(len(payload_type)).encode("ascii"),
            payload_type,
            str(len(payload)).encode("ascii"),
            payload,
        )
    )


def _cryptography() -> tuple[Any, Any, Any, Any, Any]:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    except ImportError as error:  # pragma: no cover - exercised in installations without the extra
        raise RuntimeError(
            "EEF public signatures require the 'public-signatures' optional dependency"
        ) from error
    return Ed25519PrivateKey, Ed25519PublicKey, Encoding, PublicFormat, InvalidSignature
