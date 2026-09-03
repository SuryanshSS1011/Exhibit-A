"""Credential-free public JSON passports derived from verified EEF claims."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .connectors import credential_free_source
from .eef import VerifiedClaim, read_verified_claim
from .release_evidence import public_release_projection, validate_release_evidence
from .signatures import (
    PASSPORT_PAYLOAD_TYPE,
    PASSPORT_PROFILE,
    VerifiedIdentity,
    policy_publisher,
    sign_envelope,
    verify_envelope,
)

PASSPORT_SCHEMA = "exhibit-a-passport/v1"
RELEASE_PASSPORT_SCHEMA = "exhibit-a-passport/v2"
PUBLIC_PASSPORT_SCHEMA = "exhibit-a-passport/v3"
_PASSPORT_MAC_DOMAIN = b"exhibit-a-passport/v1\0"
_RELEASE_PASSPORT_MAC_DOMAIN = b"exhibit-a-passport/v2\0"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_UNKNOWN_IDENTITIES = {"unknown_no_telemetry", "unknown_unverified_backend"}
_MAX_PROPOSAL_RUNS = 100
_MAX_EVIDENCE_SOURCES = 1000
_MAX_PASSPORT_BYTES = 1024 * 1024
_MAX_RELEASE_REASON_CHARS = 64 * 1024
_VERDICTS = {"VERIFIED", "PARTIAL", "FAILED", "UNCERTAIN"}
_EXECUTION = {"NOT_RUN", "COMPLETED", "FAILED"}
_RELEASE = {"NOT_ASSESSED", "SAFE", "UNSAFE", "UNCERTAIN"}


def create_public_passport(
    bundle: str | Path,
    output: str | Path,
    *,
    private_key_seed: bytes,
    trust_root: bytes,
    trust_anchor: bytes,
    evaluated_at: datetime | None = None,
    passport_policy_id: str,
) -> Path:
    """Verify an EEF v4 bundle and create a DSSE-signed public passport v3."""
    bundle_path = Path(bundle).resolve()
    destination = Path(output).expanduser().absolute()
    if destination == bundle_path or (
        destination.exists() and os.path.samestat(destination.stat(), bundle_path.stat())
    ):
        raise ValueError("passport output must not overwrite its EEF bundle")
    verified = read_verified_claim(
        bundle_path,
        trust_root=trust_root,
        trust_anchor=trust_anchor,
        evaluated_at=evaluated_at,
    )
    payload = _public_passport_payload(
        verified,
        passport_issuer=policy_publisher(trust_root, passport_policy_id, purpose="passport"),
    )
    envelope = sign_envelope(
        payload,
        payload_type=PASSPORT_PAYLOAD_TYPE,
        private_key_seed=private_key_seed,
        trust_root=trust_root,
        policy_id=passport_policy_id,
        purpose="passport",
    )
    encoded = json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if len(encoded.encode()) > _MAX_PASSPORT_BYTES:
        raise ValueError("public passport exceeds the 1 MiB size limit")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(destination, encoded)
    return destination


def verify_public_passport(
    envelope: bytes,
    *,
    trust_root: bytes,
    trust_anchor: bytes,
    evaluated_at: datetime | None = None,
) -> tuple[dict[str, Any], VerifiedIdentity]:
    """Verify and validate a standalone public passport v3 envelope."""
    payload, identity = verify_envelope(
        envelope,
        trust_root=trust_root,
        trust_anchor=trust_anchor,
        purpose="passport",
        evaluated_at=evaluated_at,
    )
    if payload.get("schemaVersion") != PUBLIC_PASSPORT_SCHEMA:
        raise ValueError("public passport schema is unsupported")
    if payload.get("signatureProfile") != PASSPORT_PROFILE:
        raise ValueError("public passport signature profile is invalid")
    if set(payload) != {
        "claimType",
        "passportIssuer",
        "privacy",
        "schemaVersion",
        "signatureProfile",
        "sourceEef",
        "subject",
        "verification",
    }:
        raise ValueError("public passport shape is invalid")
    source = payload.get("sourceEef")
    if (
        not isinstance(source, dict)
        or set(source) != {"format", "manifestSha256", "publisher", "trustRoot", "verifiedKeyIds"}
        or source.get("format") != "eef/v4"
    ):
        raise ValueError("public passport source EEF metadata is invalid")
    if not _SHA256.fullmatch(str(source.get("manifestSha256", ""))):
        raise ValueError("public passport manifest digest is invalid")
    publisher = source.get("publisher")
    root = source.get("trustRoot")
    key_ids = source.get("verifiedKeyIds")
    if not isinstance(publisher, dict) or set(publisher) != {"id"}:
        raise ValueError("public passport source publisher is invalid")
    if not isinstance(root, dict) or set(root) != {"rootId", "rootSha256", "rootVersion"}:
        raise ValueError("public passport source trust root is invalid")
    if not _SHA256.fullmatch(str(root.get("rootSha256", ""))):
        raise ValueError("public passport source trust root digest is invalid")
    if (
        not isinstance(key_ids, list)
        or not key_ids
        or any(not isinstance(item, str) or not item.startswith("sha256:") for item in key_ids)
    ):
        raise ValueError("public passport source key IDs are invalid")
    return payload, identity


def _public_passport_payload(
    verified: VerifiedClaim,
    *,
    passport_issuer: str,
) -> dict[str, Any]:
    identity = verified.verified_identity
    if verified.format_version != "eef/v4" or identity is None:
        raise ValueError("public passport v3 requires a publicly verified EEF v4 claim")
    if verified.claim_type == "bug_flip":
        subject = _bug_subject(verified.claim)
    elif verified.claim_type == "behavior_preserving_refactor":
        subject = _refactor_subject(verified.claim)
    else:
        raise ValueError("public passport claim type is unsupported")
    return {
        "claimType": verified.claim_type,
        "passportIssuer": {"id": passport_issuer},
        "privacy": {
            "credentialFree": True,
            "omits": [
                "source snapshots",
                "test and contract source",
                "raw execution logs",
                "repository-local paths",
                "free-form claim and model narratives",
            ],
        },
        "schemaVersion": PUBLIC_PASSPORT_SCHEMA,
        "signatureProfile": PASSPORT_PROFILE,
        "sourceEef": {
            "format": verified.format_version,
            "manifestSha256": verified.manifest_sha256,
            "publisher": {"id": identity.publisher_id},
            "trustRoot": {
                "rootId": identity.root_id,
                "rootSha256": identity.root_sha256,
                "rootVersion": identity.root_version,
            },
            "verifiedKeyIds": list(identity.verified_key_ids),
        },
        "subject": subject,
        "verification": {
            "executionReplayed": verified.verification.execution_verified,
            "integrityVerified": True,
            "publisherSignatureVerified": True,
            "meaning": "source identity is an issuer-signed claim unless the EEF is supplied",
        },
    }


def create_passport(
    bundle: str | Path,
    output: str | Path,
    *,
    signing_key: bytes,
) -> Path:
    """Verify one EEF and write its deterministic sanitized public passport."""
    if len(signing_key) < 32:
        raise ValueError("passport verification key must contain at least 32 bytes")
    bundle_path = Path(bundle).resolve()
    destination = Path(output).expanduser().absolute()
    if destination == bundle_path:
        raise ValueError("passport output must not overwrite its EEF bundle")
    if destination.exists() and os.path.samestat(destination.stat(), bundle_path.stat()):
        raise ValueError("passport output must not overwrite its EEF bundle")
    verified = read_verified_claim(bundle_path, signing_key=signing_key)
    passport = passport_from_verified_claim(verified)
    mac_domain = _passport_mac_domain(passport["schema_version"])
    passport["passport_signature"] = {
        "algorithm": "hmac-sha256",
        "value": hmac.new(
            signing_key,
            mac_domain + _canonical(passport),
            hashlib.sha256,
        ).hexdigest(),
        "meaning": "shared-key authenticity; publisher identity is not established",
    }
    encoded = (
        json.dumps(
            passport,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    if len(encoded.encode()) > _MAX_PASSPORT_BYTES:
        raise ValueError("public passport exceeds the 1 MiB size limit")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(destination, encoded)
    return destination


def verify_passport(passport: dict[str, Any], *, signing_key: bytes) -> bool:
    """Verify a standalone passport signature without requiring its private EEF."""
    if len(signing_key) < 32:
        raise ValueError("passport verification key must contain at least 32 bytes")
    if not isinstance(passport, dict):
        raise TypeError("passport must be a JSON object")
    mac_domain = _validate_passport_document(passport)
    signature = passport.get("passport_signature")
    if not isinstance(signature, dict) or set(signature) != {"algorithm", "value", "meaning"}:
        raise ValueError("passport signature is invalid")
    if signature.get("algorithm") != "hmac-sha256":
        raise ValueError("passport signature algorithm is unsupported")
    value = signature.get("value")
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError("passport signature value is invalid")
    unsigned = dict(passport)
    del unsigned["passport_signature"]
    expected = hmac.new(
        signing_key,
        mac_domain + _canonical(unsigned),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(value, expected)


def passport_from_verified_claim(verified: VerifiedClaim) -> dict[str, Any]:
    """Project a validated private claim into the allowlisted public schema."""
    if not verified.verification.integrity_verified or not verified.verification.signature_verified:
        raise ValueError("passport requires an integrity- and signature-verified EEF claim")
    if not _SHA256.fullmatch(verified.manifest_sha256):
        raise ValueError("verified EEF manifest digest is invalid")
    release_projection = None
    if verified.format_version in {"eef/v1", "eef/v2"}:
        if verified.connector_receipts:
            raise ValueError("legacy public passports cannot contain connector receipts")
        schema_version = PASSPORT_SCHEMA
    elif verified.format_version == "eef/v3":
        if verified.claim_type != "bug_flip":
            raise ValueError("release passports currently require a bug-flip claim")
        if verified.verification.execution_verified is not None:
            raise ValueError("release passport projection requires offline verification")
        validated_release = validate_release_evidence(
            verified.claim,
            verified.connector_receipts,
        )
        if validated_release is None:
            raise ValueError("EEF v3 public passport requires assessed release evidence")
        release_projection = public_release_projection(validated_release)
        schema_version = RELEASE_PASSPORT_SCHEMA
    else:
        raise ValueError(f"unsupported passport EEF format: {verified.format_version!r}")

    if verified.claim_type == "bug_flip":
        subject = _bug_subject(verified.claim)
    elif verified.claim_type == "behavior_preserving_refactor":
        subject = _refactor_subject(verified.claim)
    else:
        raise ValueError(f"unsupported passport claim type: {verified.claim_type!r}")
    passport = {
        "schema_version": schema_version,
        "claim_type": verified.claim_type,
        "subject": subject,
        "verification": {
            "eef_format": verified.format_version,
            "integrity_verified": True,
            "publisher_signature_verified": True,
            "execution_replayed": verified.verification.execution_verified,
            "manifest_sha256": verified.manifest_sha256,
            "signature": {
                "algorithm": verified.signature_algorithm,
                "value": verified.signature_value,
                "meaning": "shared-key authenticity; publisher identity is not established",
            },
        },
        "privacy": {
            "credential_free": True,
            "omits": [
                "source snapshots",
                "test and contract source",
                "raw execution logs",
                "repository-local paths",
                "free-form claim and model narratives",
            ],
        },
    }
    if release_projection is not None:
        passport["release_evidence"] = release_projection
        passport["privacy"]["omits"].extend(
            [
                "raw remote responses",
                "remote source and connector identities",
            ]
        )
    return passport


def _passport_mac_domain(schema_version: object) -> bytes:
    if schema_version == PASSPORT_SCHEMA:
        return _PASSPORT_MAC_DOMAIN
    if schema_version == RELEASE_PASSPORT_SCHEMA:
        return _RELEASE_PASSPORT_MAC_DOMAIN
    raise ValueError("passport schema version is unsupported")


def _validate_passport_document(passport: dict[str, Any]) -> bytes:
    schema_version = passport.get("schema_version")
    legacy_keys = {
        "schema_version",
        "claim_type",
        "subject",
        "verification",
        "privacy",
        "passport_signature",
    }
    if schema_version == PASSPORT_SCHEMA:
        if set(passport) != legacy_keys:
            raise ValueError("passport document shape is invalid")
        if passport.get("claim_type") not in {"bug_flip", "behavior_preserving_refactor"}:
            raise ValueError("passport claim type is invalid")
    elif schema_version == RELEASE_PASSPORT_SCHEMA:
        if set(passport) != legacy_keys | {"release_evidence"}:
            raise ValueError("passport document shape is invalid")
        if passport.get("claim_type") != "bug_flip":
            raise ValueError("release passport claim type is invalid")
        _validate_release_passport(passport)
    else:
        raise ValueError("passport document shape is invalid")
    if not all(
        isinstance(passport.get(name), dict) for name in ("subject", "verification", "privacy")
    ):
        raise ValueError("passport document sections are invalid")
    return _passport_mac_domain(schema_version)


def _validate_release_passport(passport: dict[str, Any]) -> None:
    subject = passport.get("subject")
    verification = passport.get("verification")
    privacy = passport.get("privacy")
    release = passport.get("release_evidence")
    if not all(isinstance(value, dict) for value in (subject, verification, privacy, release)):
        raise ValueError("release passport sections are invalid")
    if (
        set(verification)
        != {
            "eef_format",
            "integrity_verified",
            "publisher_signature_verified",
            "execution_replayed",
            "manifest_sha256",
            "signature",
        }
        or verification.get("eef_format") != "eef/v3"
        or verification.get("integrity_verified") is not True
        or verification.get("publisher_signature_verified") is not True
        or verification.get("execution_replayed") is not None
        or not _SHA256.fullmatch(str(verification.get("manifest_sha256", "")))
    ):
        raise ValueError("release passport verification section is invalid")
    eef_signature = verification.get("signature")
    if (
        not isinstance(eef_signature, dict)
        or set(eef_signature) != {"algorithm", "value", "meaning"}
        or eef_signature.get("algorithm") != "hmac-sha256"
        or not _SHA256.fullmatch(str(eef_signature.get("value", "")))
        or not isinstance(eef_signature.get("meaning"), str)
    ):
        raise ValueError("release passport EEF signature is invalid")
    required_omissions = {
        "raw remote responses",
        "remote source and connector identities",
    }
    if (
        set(privacy) != {"credential_free", "omits"}
        or privacy.get("credential_free") is not True
        or not isinstance(privacy.get("omits"), list)
        or not all(isinstance(item, str) for item in privacy["omits"])
        or not required_omissions.issubset(privacy["omits"])
    ):
        raise ValueError("release passport privacy section is invalid")
    truth = subject.get("truth")
    verdict = subject.get("verdict")
    _validate_release_bug_subject(subject)
    if (
        not isinstance(truth, dict)
        or set(truth) != {"execution", "goal", "release"}
        or verdict not in {"VERIFIED", "PARTIAL"}
        or truth.get("execution") != "COMPLETED"
        or truth.get("goal") != verdict
    ):
        raise ValueError("release passport claim truth is invalid")
    _validate_public_release_evidence(release, truth.get("release"))


def _validate_release_bug_subject(subject: dict[str, Any]) -> None:
    if set(subject) != {
        "case_id_sha256",
        "verdict",
        "truth",
        "deterministic",
        "reruns",
        "test_sha256",
        "proposal_runs",
        "proposal_runs_omitted",
        "evidence_sources",
        "evidence_sources_omitted",
        "revisions",
    }:
        raise ValueError("release passport subject shape is invalid")
    if (
        not _SHA256.fullmatch(str(subject.get("case_id_sha256", "")))
        or not _SHA256.fullmatch(str(subject.get("test_sha256", "")))
        or subject.get("deterministic") is not True
        or not _passport_int(subject.get("reruns"), minimum=1, maximum=20)
        or not _passport_int(subject.get("proposal_runs_omitted"))
        or not _passport_int(subject.get("evidence_sources_omitted"))
    ):
        raise ValueError("release passport subject evidence is invalid")
    proposals = subject.get("proposal_runs")
    sources = subject.get("evidence_sources")
    revisions = subject.get("revisions")
    if (
        not isinstance(proposals, list)
        or len(proposals) > _MAX_PROPOSAL_RUNS
        or not all(_valid_public_proposal(item) for item in proposals)
        or not isinstance(sources, list)
        or len(sources) > _MAX_EVIDENCE_SOURCES
        or not all(_valid_public_evidence_source(item) for item in sources)
        or not isinstance(revisions, dict)
        or not all(
            name in {"base_commit", "target_commit", "culprit_commit", "culprit_parent_commit"}
            and isinstance(revision, str)
            and re.fullmatch(r"[0-9a-f]{7,64}", revision)
            for name, revision in revisions.items()
        )
    ):
        raise ValueError("release passport subject evidence is invalid")


def _valid_public_proposal(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "operation",
        "provider",
        "requested_model",
        "confirmed_model",
        "confirmed_version",
        "output_sha256",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "tool_call_count",
    }:
        return False
    return (
        value.get("operation") in {"propose", "refine"}
        and all(
            _public_identity(value.get(name))
            for name in ("provider", "requested_model", "confirmed_model", "confirmed_version")
        )
        and _SHA256.fullmatch(str(value.get("output_sha256", ""))) is not None
        and all(
            item is None or _passport_int(item, maximum=10**12)
            for item in (
                value.get("input_tokens"),
                value.get("output_tokens"),
                value.get("total_tokens"),
            )
        )
        and _passport_int(value.get("tool_call_count"), maximum=10**9)
    )


def _valid_public_evidence_source(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "evidence_id",
        "connector_id",
        "connector_version",
        "capability",
        "source",
        "request_sha256",
        "response_sha256",
        "artifact_sha256",
        "content_sha256",
    }:
        return False
    return (
        all(
            _public_identity(value.get(name))
            for name in ("evidence_id", "connector_id", "connector_version", "capability")
        )
        and (value.get("source") == "local-checkout" or _public_identity(value.get("source")))
        and all(
            _SHA256.fullmatch(str(value.get(name, ""))) is not None
            for name in (
                "request_sha256",
                "response_sha256",
                "artifact_sha256",
                "content_sha256",
            )
        )
    )


def _public_identity(value: object) -> bool:
    return value in _UNKNOWN_IDENTITIES or (
        isinstance(value, str)
        and value.startswith("sha256:")
        and _SHA256.fullmatch(value.removeprefix("sha256:")) is not None
    )


def _passport_int(value: object, *, minimum: int = 0, maximum: int = 1000) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def _validate_public_release_evidence(value: dict[str, Any], subject_release: object) -> None:
    if (
        set(value)
        != {
            "schema_version",
            "policy",
            "checks",
            "collection",
            "freshness",
            "provenance",
            "release",
            "reason",
        }
        or value.get("schema_version") != "release-evidence/v1"
    ):
        raise ValueError("release passport evidence shape is invalid")
    policy = value.get("policy")
    checks = value.get("checks")
    collection = value.get("collection")
    freshness = value.get("freshness")
    provenance = value.get("provenance")
    if not isinstance(policy, dict) or set(policy) != {
        "schema_version",
        "name",
        "required_checks",
        "max_age_s",
        "sha256",
    }:
        raise ValueError("release passport policy is invalid")
    required_checks = policy.get("required_checks")
    max_age_s = policy.get("max_age_s")
    if (
        policy.get("schema_version") != "release-policy/v1"
        or not isinstance(policy.get("name"), str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", policy["name"])
        or not isinstance(required_checks, list)
        or not 1 <= len(required_checks) <= 128
        or len(set(required_checks)) != len(required_checks)
        or not all(
            isinstance(name, str) and name.strip() == name and 1 <= len(name) <= 256
            for name in required_checks
        )
        or not isinstance(max_age_s, int)
        or isinstance(max_age_s, bool)
        or not 1 <= max_age_s <= 31 * 24 * 60 * 60
    ):
        raise ValueError("release passport policy is invalid")
    policy_without_digest = {
        name: policy[name] for name in ("schema_version", "name", "required_checks", "max_age_s")
    }
    if policy.get("sha256") != hashlib.sha256(_canonical(policy_without_digest)).hexdigest():
        raise ValueError("release passport policy digest is invalid")
    if not isinstance(checks, list) or len(checks) > 250:
        raise ValueError("release passport checks are invalid")
    projected_names = []
    for check in checks:
        if not isinstance(check, dict) or set(check) != {
            "name",
            "status",
            "conclusion",
            "started_at",
            "completed_at",
        }:
            raise ValueError("release passport check is invalid")
        name = check.get("name")
        status = check.get("status")
        conclusion = check.get("conclusion")
        if (
            name not in required_checks
            or not isinstance(status, str)
            or not 1 <= len(status) <= 64
            or conclusion is not None
            and (not isinstance(conclusion, str) or len(conclusion) > 64)
        ):
            raise ValueError("release passport check is invalid")
        for timestamp in (check.get("started_at"), check.get("completed_at")):
            if timestamp is not None:
                _passport_timestamp(timestamp, canonical=False)
        projected_names.append(name)
    if not isinstance(collection, dict) or set(collection) != {
        "reported_total",
        "collected_count",
        "omitted_check_count",
        "all_check_names_unique",
    }:
        raise ValueError("release passport collection summary is invalid")
    reported_total = collection.get("reported_total")
    collected_count = collection.get("collected_count")
    omitted_check_count = collection.get("omitted_check_count")
    if (
        not isinstance(reported_total, int)
        or isinstance(reported_total, bool)
        or reported_total < 0
        or not isinstance(collected_count, int)
        or isinstance(collected_count, bool)
        or not len(checks) <= collected_count <= 250
        or not isinstance(omitted_check_count, int)
        or isinstance(omitted_check_count, bool)
        or omitted_check_count != collected_count - len(checks)
        or not isinstance(collection.get("all_check_names_unique"), bool)
    ):
        raise ValueError("release passport collection summary is invalid")
    if not isinstance(freshness, dict) or set(freshness) != {
        "basis",
        "observed_at",
        "source_updated_at",
        "evaluated_at",
    }:
        raise ValueError("release passport freshness is invalid")
    if freshness.get("basis") != "point_in_time":
        raise ValueError("release passport freshness is invalid")
    observed_at = _passport_timestamp(freshness.get("observed_at"), canonical=True)
    evaluated_at = _passport_timestamp(freshness.get("evaluated_at"), canonical=True)
    if freshness.get("source_updated_at") is not None:
        _passport_timestamp(freshness["source_updated_at"], canonical=False)
    if (
        not isinstance(provenance, dict)
        or set(provenance)
        != {
            "request_sha256",
            "response_sha256",
            "artifact_sha256",
            "content_sha256",
        }
        or not all(_SHA256.fullmatch(str(digest)) for digest in provenance.values())
    ):
        raise ValueError("release passport provenance is invalid")
    release = value.get("release")
    reason = value.get("reason")
    if (
        release not in {"SAFE", "UNSAFE", "UNCERTAIN"}
        or release != subject_release
        or not isinstance(reason, str)
        or not reason
        or len(reason) > _MAX_RELEASE_REASON_CHARS
    ):
        raise ValueError("release passport truth is inconsistent")
    derived_release, derived_reason = _derive_public_release_truth(
        checks=checks,
        required_checks=required_checks,
        collection=collection,
        observed_at=observed_at,
        evaluated_at=evaluated_at,
        max_age_s=max_age_s,
    )
    if release != derived_release or reason != derived_reason:
        raise ValueError("release passport public reason is inconsistent")
    names_complete = len(projected_names) == len(required_checks) and set(projected_names) == set(
        required_checks
    )
    names_unique = len(projected_names) == len(set(projected_names))
    if release in {"SAFE", "UNSAFE"} and (
        not names_complete
        or not names_unique
        or reported_total != collected_count
        or collection["all_check_names_unique"] is not True
        or not 0 <= (evaluated_at - observed_at).total_seconds() <= max_age_s
    ):
        raise ValueError("release passport truth is inconsistent")
    if release == "SAFE" and not all(
        check["status"] == "completed"
        and check["conclusion"] == "success"
        and _completion_is_fresh(check, observed_at, evaluated_at, max_age_s)
        for check in checks
    ):
        raise ValueError("release passport SAFE truth is inconsistent")
    failure_conclusions = {
        "failure",
        "cancelled",
        "timed_out",
        "action_required",
        "startup_failure",
    }
    if release == "UNSAFE" and not any(
        check["status"] == "completed"
        and check["conclusion"] in failure_conclusions
        and _completion_is_fresh(check, observed_at, evaluated_at, max_age_s)
        for check in checks
    ):
        raise ValueError("release passport UNSAFE truth is inconsistent")


def _derive_public_release_truth(
    *,
    checks: list[dict[str, Any]],
    required_checks: list[str],
    collection: dict[str, Any],
    observed_at: datetime,
    evaluated_at: datetime,
    max_age_s: int,
) -> tuple[str, str]:
    observation_age = (evaluated_at - observed_at).total_seconds()
    if observation_age < 0:
        return "UNCERTAIN", "CI observation time is in the future"
    if observation_age > max_age_s:
        return "UNCERTAIN", "CI status observation is older than the policy allows"
    if collection["reported_total"] != collection["collected_count"]:
        return "UNCERTAIN", "CI status observation is incomplete"
    if collection["all_check_names_unique"] is not True:
        return "UNCERTAIN", "CI status contains duplicate check names"

    indexed = {check["name"]: check for check in checks}
    missing = [name for name in required_checks if name not in indexed]
    if missing:
        return "UNCERTAIN", f"required CI checks are missing: {', '.join(missing)}"

    failures = []
    indeterminate = []
    failure_conclusions = {
        "failure",
        "cancelled",
        "timed_out",
        "action_required",
        "startup_failure",
    }
    for name in required_checks:
        check = indexed[name]
        timing_issue = _public_completion_issue(
            check,
            observed_at=observed_at,
            evaluated_at=evaluated_at,
            max_age_s=max_age_s,
        )
        if timing_issue is not None:
            indeterminate.append(f"{name} ({timing_issue})")
        elif check["status"] != "completed":
            indeterminate.append(f"{name} ({check['status']})")
        elif check["conclusion"] == "success":
            continue
        elif check["conclusion"] in failure_conclusions:
            failures.append(f"{name} ({check['conclusion']})")
        else:
            indeterminate.append(f"{name} ({check['conclusion'] or 'no conclusion'})")
    if failures:
        return "UNSAFE", f"required CI checks failed: {', '.join(failures)}"
    if indeterminate:
        return (
            "UNCERTAIN",
            f"required CI checks are not conclusively successful: {', '.join(indeterminate)}",
        )
    return (
        "SAFE",
        f"all required CI checks completed successfully: {', '.join(required_checks)}",
    )


def _public_completion_issue(
    check: dict[str, Any],
    *,
    observed_at: datetime,
    evaluated_at: datetime,
    max_age_s: int,
) -> str | None:
    if check["status"] != "completed":
        return None
    completed_at = check.get("completed_at")
    if completed_at is None:
        return "invalid completion time"
    completed = _passport_timestamp(completed_at, canonical=False)
    if completed > observed_at or completed > evaluated_at:
        return "completion time is in the future"
    if (evaluated_at - completed).total_seconds() > max_age_s:
        return "completion is stale"
    return None


def _completion_is_fresh(
    check: dict[str, Any],
    observed_at: datetime,
    evaluated_at: datetime,
    max_age_s: int,
) -> bool:
    completed_at = check.get("completed_at")
    if completed_at is None:
        return False
    completed = _passport_timestamp(completed_at, canonical=False)
    return (
        completed <= observed_at
        and completed <= evaluated_at
        and (evaluated_at - completed).total_seconds() <= max_age_s
    )


def _passport_timestamp(value: object, *, canonical: bool) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError("release passport timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("release passport timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("release passport timestamp must be timezone-aware")
    parsed = parsed.astimezone(timezone.utc)
    if canonical and value != parsed.isoformat():
        raise ValueError("release passport timestamp must use canonical UTC form")
    return parsed


def _bug_subject(case: dict[str, Any]) -> dict[str, Any]:
    verdict = _enum(case.get("verdict"), _VERDICTS, "bug verdict")
    truth = case.get("truth")
    if not isinstance(truth, dict):
        raise TypeError("bug passport requires truth separation")
    evidence = case.get("evidence")
    if not isinstance(evidence, dict):
        raise TypeError("bug passport requires evidence metadata")
    deterministic = evidence.get("deterministic")
    if not isinstance(deterministic, bool):
        raise TypeError("bug passport determinism must be boolean")
    reruns = _nonnegative_int(evidence.get("reruns"), "bug reruns")
    test = case.get("test_file")
    if not isinstance(test, dict) or not isinstance(test.get("code"), str):
        raise TypeError("bug passport requires a test artifact")
    proposal_runs = case.get("proposal_runs", [])
    if not isinstance(proposal_runs, list):
        raise TypeError("bug passport proposal runs must be a list")
    evidence_sources = case.get("evidence_sources", [])
    if not isinstance(evidence_sources, list):
        raise TypeError("bug passport evidence sources must be a list")
    return {
        "case_id_sha256": _identity_digest(case.get("id"), "case"),
        "verdict": verdict,
        "truth": _truth(truth),
        "deterministic": deterministic,
        "reruns": reruns,
        "test_sha256": hashlib.sha256(test["code"].encode()).hexdigest(),
        "proposal_runs": [_proposal_run(item) for item in proposal_runs[:_MAX_PROPOSAL_RUNS]],
        "proposal_runs_omitted": max(0, len(proposal_runs) - _MAX_PROPOSAL_RUNS),
        "evidence_sources": [
            _evidence_source(item) for item in evidence_sources[:_MAX_EVIDENCE_SOURCES]
        ],
        "evidence_sources_omitted": max(0, len(evidence_sources) - _MAX_EVIDENCE_SOURCES),
        "revisions": _revisions(case),
    }


def _refactor_subject(evidence: dict[str, Any]) -> dict[str, Any]:
    result = evidence.get("result")
    if not isinstance(result, dict):
        raise TypeError("refactor passport requires a result")
    deterministic = result.get("deterministic")
    if not isinstance(deterministic, bool):
        raise TypeError("refactor passport determinism must be boolean")
    sources = evidence.get("evidence_sources")
    if not isinstance(sources, list):
        raise TypeError("refactor passport evidence sources must be a list")
    contract_sha256 = evidence.get("contract_sha256")
    if not isinstance(contract_sha256, str) or not _SHA256.fullmatch(contract_sha256):
        raise ValueError("refactor passport contract digest is invalid")
    return {
        "evidence_schema": _identity_commitment(evidence.get("schema_version"), "schema"),
        "verdict": _enum(result.get("verdict"), _VERDICTS, "refactor verdict"),
        "truth": {
            "execution": _enum(result.get("execution"), _EXECUTION, "execution truth"),
            "goal": _enum(result.get("goal"), _VERDICTS, "goal truth"),
            "release": _enum(result.get("release"), _RELEASE, "release truth"),
        },
        "deterministic": deterministic,
        "reruns_per_state": _nonnegative_int(evidence.get("reruns"), "refactor reruns"),
        "contract_sha256": contract_sha256,
        "states": {
            "base": _state_observation(result.get("base"), "base"),
            "target": _state_observation(result.get("target"), "target"),
        },
        "evidence_sources": [_evidence_source(item) for item in sources[:_MAX_EVIDENCE_SOURCES]],
        "evidence_sources_omitted": max(0, len(sources) - _MAX_EVIDENCE_SOURCES),
        "revisions": _refactor_revisions(sources),
    }


def _truth(truth: dict[str, Any]) -> dict[str, str]:
    return {
        "execution": _enum(truth.get("execution"), _EXECUTION, "execution truth"),
        "goal": _enum(truth.get("goal"), _VERDICTS, "goal truth"),
        "release": _enum(truth.get("release"), _RELEASE, "release truth"),
    }


def _state_observation(value: object, state: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"refactor passport requires a {state} observation")
    status = _enum(
        value.get("status"),
        {"NOT_RUN", "PASS", "FAIL", "FLAKY", "INFRA"},
        f"{state} status",
    )
    exit_codes = value.get("exit_codes")
    if not isinstance(exit_codes, list) or not all(
        isinstance(code, int) and not isinstance(code, bool) for code in exit_codes
    ):
        raise ValueError(f"refactor passport {state} exit codes are invalid")
    return {
        "status": status,
        "runs": _nonnegative_int(value.get("runs"), f"{state} runs"),
        "exit_codes": exit_codes,
    }


def _proposal_run(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("bug passport proposal run is invalid")
    digest = value.get("output_sha256")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ValueError("bug passport proposal output digest is invalid")
    return {
        "operation": _enum(value.get("operation"), {"propose", "refine"}, "operation"),
        "provider": _identity_commitment(value.get("provider"), "provider"),
        "requested_model": _identity_commitment(value.get("requested_model"), "model"),
        "confirmed_model": _identity_commitment(value.get("confirmed_model"), "model"),
        "confirmed_version": _identity_commitment(value.get("confirmed_version"), "version"),
        "output_sha256": digest,
        "input_tokens": _optional_nonnegative_int(value.get("input_tokens")),
        "output_tokens": _optional_nonnegative_int(value.get("output_tokens")),
        "total_tokens": _optional_nonnegative_int(value.get("total_tokens")),
        "tool_call_count": _nonnegative_int(value.get("tool_call_count", 0), "tool call count"),
    }


def _evidence_source(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("passport evidence source is invalid")
    digests = {}
    for name in ("request_sha256", "response_sha256", "artifact_sha256", "content_sha256"):
        digest = value.get(name)
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError(f"passport evidence source {name} is invalid")
        digests[name] = digest
    return {
        "evidence_id": _identity_commitment(value.get("evidence_id"), "evidence"),
        "connector_id": _identity_commitment(value.get("connector_id"), "connector"),
        "connector_version": _identity_commitment(value.get("connector_version"), "version"),
        "capability": _identity_commitment(value.get("capability"), "capability"),
        "source": _public_source(
            value.get("source") if isinstance(value.get("source"), str) else None
        ),
        **digests,
    }


def _revisions(case: dict[str, Any]) -> dict[str, str]:
    revisions = {}
    for name in ("base_commit", "target_commit", "culprit_commit", "culprit_parent_commit"):
        value = case.get(name)
        if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{7,64}", value):
            revisions[name] = value.lower()
    return revisions


def _refactor_revisions(sources: list[Any]) -> dict[str, str]:
    """Derive the tested revisions from the per-state connector receipts.

    A refactor Case carries no commit fields of its own; the only credential-free record
    of what was executed is each receipt's ``source_revision``. Reruns of one state must
    agree — a disagreement means the archive mixes revisions and must not be flattened
    into a single claim about that state.
    """
    revisions: dict[str, str] = {}
    for state in ("base", "target"):
        description = f"Executed the configured test against the {state} code state"
        observed = {
            item["source_revision"]
            for item in sources
            if isinstance(item, dict)
            and item.get("description") == description
            and isinstance(item.get("source_revision"), str)
        }
        if len(observed) > 1:
            raise ValueError(f"refactor passport {state} evidence mixes source revisions")
        for value in observed:
            if re.fullmatch(r"[0-9a-fA-F]{7,64}", value):
                revisions[f"{state}_commit"] = value.lower()
    return revisions


def _identity_commitment(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"passport {label} identity is missing")
    if value in _UNKNOWN_IDENTITIES:
        return value
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def _identity_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"passport {label} identity is missing")
    return hashlib.sha256(value.encode()).hexdigest()


def _public_source(value: str | None) -> str:
    source = credential_free_source(value)
    if source == "local-checkout":
        return source
    return f"sha256:{hashlib.sha256(source.encode()).hexdigest()}"


def _atomic_write(destination: Path, encoded: str) -> None:
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as stream:
            temporary = stream.name
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _enum(value: object, allowed: set[str], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"passport {label} is invalid")
    return value


def _nonnegative_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"passport {label} must be a non-negative integer")
    return value


def _optional_nonnegative_int(value: object) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, "token count")
