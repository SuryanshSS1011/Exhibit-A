"""Credential-free public JSON passports derived from verified EEF claims."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .connectors import credential_free_source
from .eef import VerifiedClaim, read_verified_claim

PASSPORT_SCHEMA = "exhibit-a-passport/v1"
_PASSPORT_MAC_DOMAIN = b"exhibit-a-passport/v1\0"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_UNKNOWN_IDENTITIES = {"unknown_no_telemetry", "unknown_unverified_backend"}
_MAX_PROPOSAL_RUNS = 100
_MAX_EVIDENCE_SOURCES = 1000
_MAX_PASSPORT_BYTES = 1024 * 1024
_VERDICTS = {"VERIFIED", "PARTIAL", "FAILED", "UNCERTAIN"}
_EXECUTION = {"NOT_RUN", "COMPLETED", "FAILED"}
_RELEASE = {"NOT_ASSESSED", "SAFE", "UNSAFE", "UNCERTAIN"}


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
    passport["passport_signature"] = {
        "algorithm": "hmac-sha256",
        "value": hmac.new(
            signing_key,
            _PASSPORT_MAC_DOMAIN + _canonical(passport),
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
    if (
        set(passport)
        != {
            "schema_version",
            "claim_type",
            "subject",
            "verification",
            "privacy",
            "passport_signature",
        }
        or passport.get("schema_version") != PASSPORT_SCHEMA
    ):
        raise ValueError("passport document shape is invalid")
    if passport.get("claim_type") not in {"bug_flip", "behavior_preserving_refactor"}:
        raise ValueError("passport claim type is invalid")
    if not all(
        isinstance(passport.get(name), dict) for name in ("subject", "verification", "privacy")
    ):
        raise ValueError("passport document sections are invalid")
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
        _PASSPORT_MAC_DOMAIN + _canonical(unsigned),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(value, expected)


def passport_from_verified_claim(verified: VerifiedClaim) -> dict[str, Any]:
    """Project a validated private claim into the allowlisted public schema."""
    if not verified.verification.integrity_verified or not verified.verification.signature_verified:
        raise ValueError("passport requires an integrity- and signature-verified EEF claim")
    if not _SHA256.fullmatch(verified.manifest_sha256):
        raise ValueError("verified EEF manifest digest is invalid")
    if verified.claim_type == "bug_flip":
        subject = _bug_subject(verified.claim)
    elif verified.claim_type == "behavior_preserving_refactor":
        subject = _refactor_subject(verified.claim)
    else:
        raise ValueError(f"unsupported passport claim type: {verified.claim_type!r}")
    return {
        "schema_version": PASSPORT_SCHEMA,
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
