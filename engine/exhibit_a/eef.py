"""Executable Evidence Format (EEF) deterministic bundle reference implementation."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shlex
import stat
import subprocess
import tempfile
import threading
import unicodedata
import uuid
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .verdict.refactor_runner import RefactorEvidence

from .connectors.base import ConnectorOutput
from .connectors.ci_status import CIStatus
from .eef_receipts import (
    RECEIPT_ENTRY,
    ReceiptArchive,
    build_receipt_archive,
    claim_binding,
    validate_receipt_archive,
    validate_receipt_metadata,
)
from .executor.base import ExecOutcome
from .models.case import Verdict, normalize_case_payload, normalize_verdict
from .release_evidence import validate_release_evidence
from .replay_environment import (
    PINNED_PYTEST_VERSION,
    ReplayEnvironment,
    inspect_local_replay_image,
    parse_replay_environment,
)
from .signatures import (
    EEF_PAYLOAD_TYPE,
    EEF_PROFILE,
    EEF_PREDICATE_TYPE,
    VerifiedIdentity,
    canonical_json,
    policy_publisher,
    sign_envelope,
    verify_envelope,
)
from .verdict.flip_check import flip_check

FORMAT_VERSION = "eef/v2"
RECEIPT_FORMAT_VERSION = "eef/v3"
PUBLIC_FORMAT_VERSION = "eef/v4"
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://exhibit-a.dev/eef/v2"
RECEIPT_PREDICATE_TYPE = "https://exhibit-a.dev/eef/v3"
_LEGACY_FORMAT_VERSION = "eef/v1"
_LEGACY_PREDICATE_TYPE = "https://exhibit-a.dev/eef/v1"
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_EXCLUDED_PARTS = {".git", ".exhibit-a", "__pycache__", ".env"}
_MAX_ARCHIVE_ENTRIES = 10_000
_MAX_ENTRY_BYTES = 64 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
_MAX_RERUNS = 20
_BUILD_TIMEOUT_S = 300
_RUN_TIMEOUT_S = 120
_CLEANUP_TIMEOUT_S = 30
_OUTPUT_LIMIT_BYTES = 8 * 1024 * 1024
REPLAY_ENVIRONMENT_ENTRY = "environment.json"
_ALLOWED_PYTEST_FLAGS = {"-x", "-q", "--tb=short", "--disable-warnings"}
_ALLOWED_PYTHON_BINS = {"python", "python3", "python3.11", "python3.12"}
_ALLOWED_PYTEST_BINS = {"pytest", "pytest3"}


@dataclass(frozen=True)
class VerificationResult:
    integrity_verified: bool
    signature_verified: bool
    execution_verified: bool | None


@dataclass(frozen=True)
class VerifiedClaim:
    """One fully validated signed claim, without source or raw-log payloads."""

    verification: VerificationResult
    format_version: str
    claim_type: str
    claim: dict[str, Any]
    manifest_sha256: str
    signature_algorithm: str
    signature_value: str
    archived_states: tuple[str, ...]
    connector_receipts: tuple[dict[str, Any], ...] = ()
    verified_identity: VerifiedIdentity | None = None


@dataclass
class SourceSnapshotBudget:
    """Shared incremental limits for source trees materialized into one EEF."""

    entries: int = 0
    total_bytes: int = 0

    def consume(self, size: int) -> None:
        if size < 0 or size > _MAX_ENTRY_BYTES:
            raise ValueError("EEF source entry exceeds the size limit")
        self.entries += 1
        self.total_bytes += size
        if self.entries > _MAX_ARCHIVE_ENTRIES:
            raise ValueError("EEF source snapshots contain too many entries")
        if self.total_bytes > _MAX_ARCHIVE_BYTES:
            raise ValueError("EEF source snapshots exceed the total size limit")


def create_bundle(
    case: Mapping[str, Any],
    output: str | Path,
    *,
    target_source: str | Path,
    base_source: str | Path | None,
    signing_key: bytes | None = None,
    private_key_seed: bytes | None = None,
    trust_root: bytes | None = None,
    policy_id: str | None = None,
    replay_environment: Mapping[str, Any] | None = None,
    connector_outputs: Sequence[ConnectorOutput[CIStatus]] = (),
) -> Path:
    """Serialize a Case plus source snapshots into a deterministic signed archive."""
    _validate_signing_inputs(signing_key, private_key_seed, trust_root, policy_id)
    environment = _select_replay_environment(private_key_seed, replay_environment)
    case = normalize_case_payload(case)
    test = case.get("test_file")
    if not isinstance(test, Mapping) or not isinstance(test.get("path"), str):
        raise ValueError("EEF requires a Case with a generated test_file")
    test_path = _safe_relative(str(test["path"]))
    test_code = str(test.get("code", ""))
    run_argv = _safe_pytest_argv(str(case.get("run_command", "")), str(test_path))

    evidence = case.get("evidence", {})
    if not isinstance(evidence, Mapping):
        raise ValueError("EEF Case evidence is invalid")
    reruns = _bounded_int(evidence.get("reruns", 1), "reruns", 1, _MAX_RERUNS)
    case_content = _canonical(case) + b"\n"
    payloads: dict[str, bytes] = {
        "case.json": case_content,
        "reproduce.json": _canonical(
            {
                "command_argv": run_argv,
                "expected_signature": case.get("evidence", {}).get("fail_signature"),
                "reruns": reruns,
                "verdict": case.get("verdict"),
            }
        )
        + b"\n",
    }
    _add_source(payloads, Path(target_source), "target", test_path, test_code)
    if base_source is not None:
        _add_source(payloads, Path(base_source), "base", test_path, test_code)
    for field in ("fail_log", "pass_log", "control_log", "bisect_log"):
        payloads[f"logs/{field}.txt"] = str(evidence.get(field, "")).encode()
    payloads["logs/existing_suite_log.txt"] = str(case.get("existing_suite_log", "")).encode()
    payloads["Dockerfile"] = _dockerfile(run_argv, environment).encode()
    if environment is not None:
        payloads[REPLAY_ENVIRONMENT_ENTRY] = _canonical(environment.to_dict()) + b"\n"

    format_version = FORMAT_VERSION
    receipt_archive = None
    if connector_outputs:
        binding = claim_binding(
            claim_type="bug_flip",
            claim_content=case_content,
            repository_source=case.get("repo"),
            revision=case.get("target_commit"),
        )
        receipt_archive = build_receipt_archive(connector_outputs, binding)
        payloads[RECEIPT_ENTRY] = receipt_archive.content
        format_version = RECEIPT_FORMAT_VERSION

    validate_release_evidence(
        case,
        receipt_archive.receipts if receipt_archive else (),
    )

    receipt_metadata = receipt_archive.manifest_metadata if receipt_archive else {}

    return _write_bundle(
        payloads,
        output,
        signing_key,
        private_key_seed=private_key_seed,
        trust_root=trust_root,
        policy_id=policy_id,
        format_version=format_version,
        manifest_metadata={
            "claim_type": "bug_flip",
            "case_id": case.get("id"),
            **receipt_metadata,
        },
        predicate={
            "claim_type": "bug_flip",
            "case_id": case.get("id"),
            "verdict": case.get("verdict"),
            "created_at": case.get("created_at"),
            **receipt_metadata,
        },
    )


def create_refactor_bundle(
    evidence: RefactorEvidence,
    output: str | Path,
    *,
    base_source: str | Path,
    target_source: str | Path,
    signing_key: bytes | None = None,
    private_key_seed: bytes | None = None,
    trust_root: bytes | None = None,
    policy_id: str | None = None,
    replay_environment: Mapping[str, Any] | None = None,
    output_limit_bytes: int = _OUTPUT_LIMIT_BYTES,
    connector_outputs: Sequence[ConnectorOutput[CIStatus]] = (),
) -> Path:
    """Serialize behavior-refactor evidence through its claim-specific EEF adapter."""
    from .eef_refactor import create_refactor_bundle as create

    return create(
        evidence,
        output,
        base_source=base_source,
        target_source=target_source,
        signing_key=signing_key,
        private_key_seed=private_key_seed,
        trust_root=trust_root,
        policy_id=policy_id,
        replay_environment=replay_environment,
        output_limit_bytes=output_limit_bytes,
        connector_outputs=connector_outputs,
    )


def verify_bundle(
    bundle: str | Path,
    *,
    signing_key: bytes | None = None,
    trust_root: bytes | None = None,
    trust_anchor: bytes | None = None,
    evaluated_at: datetime | None = None,
    execute: bool = False,
    docker_bin: str = "docker",
) -> VerificationResult:
    """Verify all hashes/signature and optionally re-execute via the flip judge."""
    return _verify_bundle(
        bundle,
        signing_key=signing_key,
        trust_root=trust_root,
        trust_anchor=trust_anchor,
        evaluated_at=evaluated_at,
        execute=execute,
        docker_bin=docker_bin,
    ).verification


def read_verified_claim(
    bundle: str | Path,
    *,
    signing_key: bytes | None = None,
    trust_root: bytes | None = None,
    trust_anchor: bytes | None = None,
    evaluated_at: datetime | None = None,
) -> VerifiedClaim:
    """Return the validated claim needed for a public passport without replaying it."""
    verified = _verify_bundle(
        bundle,
        signing_key=signing_key,
        trust_root=trust_root,
        trust_anchor=trust_anchor,
        evaluated_at=evaluated_at,
        execute=False,
        docker_bin="docker",
    )
    if verified.claim_type == "bug_flip":
        _validate_public_bug_truth(
            verified.claim,
            archived_states=verified.archived_states,
            connector_receipts=verified.connector_receipts,
        )
    return verified


def _validate_public_bug_truth(
    case: dict[str, Any],
    *,
    archived_states: tuple[str, ...],
    connector_receipts: tuple[dict[str, Any], ...],
) -> None:
    """Re-derive an admissible bug claim before it can enter a public passport."""
    evidence = case.get("evidence")
    truth = case.get("truth")
    test = case.get("test_file")
    if not isinstance(evidence, dict) or not isinstance(truth, dict):
        raise TypeError("EEF public bug claim is missing evidence truth metadata")
    if not isinstance(test, dict) or not isinstance(test.get("code"), str):
        raise TypeError("EEF public bug claim is missing its test artifact")
    runs = evidence.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("EEF public bug claim requires structured execution records")

    outcomes: dict[str, list[ExecOutcome]] = {"target": [], "base": [], "control": []}
    for record in runs:
        if not isinstance(record, dict) or record.get("state") not in outcomes:
            raise ValueError("EEF public bug claim contains an invalid execution record")
        exit_code = record.get("exit_code")
        passed = record.get("passed")
        log = record.get("log")
        if (
            not isinstance(exit_code, int)
            or isinstance(exit_code, bool)
            or not isinstance(passed, bool)
            or not isinstance(log, str)
            or passed != (exit_code == 0)
        ):
            raise ValueError("EEF public bug claim contains an inconsistent execution record")
        outcomes[record["state"]].append(ExecOutcome(exit_code, log, ""))

    reruns = _bounded_int(evidence.get("reruns"), "Case reruns", 1, _MAX_RERUNS)
    if len(outcomes["target"]) != reruns:
        raise ValueError("EEF public bug target records do not match its rerun count")
    if len(outcomes["base"]) > 1 or len(outcomes["control"]) > 1:
        raise ValueError("EEF public bug claim contains duplicate comparison records")
    verdict = normalize_verdict(case.get("verdict"))
    has_base_state = "base" in archived_states
    has_base_record = bool(outcomes["base"])
    if verdict is Verdict.VERIFIED and not (has_base_state and has_base_record):
        raise ValueError("EEF public VERIFIED bug claim requires an archived base state")
    if verdict is Verdict.PARTIAL and (has_base_state or has_base_record):
        raise ValueError("EEF public PARTIAL bug claim must not claim a base state")
    flip = flip_check(
        target_runs=outcomes["target"],
        base_run=outcomes["base"][0] if outcomes["base"] else None,
        control_run=outcomes["control"][0] if outcomes["control"] else None,
        test_code=test["code"],
        expected_signature=evidence.get("fail_signature"),
        allow_reproduced=verdict is Verdict.PARTIAL,
    )
    expected_tier = "flip" if verdict is Verdict.VERIFIED else "reproduced"
    if verdict not in {Verdict.VERIFIED, Verdict.PARTIAL} or not flip.admissible:
        raise ValueError("EEF public bug claim is not admissible evidence")
    if flip.tier != expected_tier or evidence.get("deterministic") is not True:
        raise ValueError("EEF public bug verdict does not match its execution records")
    release_evidence = validate_release_evidence(case, connector_receipts)
    expected_truth = {
        "execution": "COMPLETED",
        "goal": verdict.value,
        "release": (
            release_evidence.assessment.release.value
            if release_evidence is not None
            else "NOT_ASSESSED"
        ),
    }
    if {name: truth.get(name) for name in expected_truth} != expected_truth:
        raise ValueError("EEF public bug truth does not match its derived verdict")


def _verify_bundle(
    bundle: str | Path,
    *,
    signing_key: bytes | None,
    trust_root: bytes | None,
    trust_anchor: bytes | None,
    evaluated_at: datetime | None,
    execute: bool,
    docker_bin: str,
) -> VerifiedClaim:
    with zipfile.ZipFile(bundle) as archive:
        infos = archive.infolist()
        if len(infos) > _MAX_ARCHIVE_ENTRIES:
            raise ValueError("EEF contains too many entries")
        total_size = 0
        names = []
        for info in infos:
            _validate_zip_entry(info)
            total_size += info.file_size
            if total_size > _MAX_ARCHIVE_BYTES:
                raise ValueError("EEF exceeds the total uncompressed size limit")
            names.append(info.filename)
        if len(names) != len(set(names)):
            raise ValueError("EEF contains duplicate paths")
        _reject_parent_collisions(names)
        blobs = {name: archive.read(name) for name in names}
    try:
        manifest = json.loads(blobs["manifest.json"])
    except KeyError as exc:
        raise ValueError(f"EEF is missing required entry: {exc.args[0]}") from exc
    if "attestation.json" not in blobs:
        raise ValueError("EEF is missing required entry: attestation.json")
    if not isinstance(manifest, dict) or manifest.get("format") not in {
        FORMAT_VERSION,
        RECEIPT_FORMAT_VERSION,
        PUBLIC_FORMAT_VERSION,
        _LEGACY_FORMAT_VERSION,
    }:
        raise ValueError("EEF manifest format is unsupported")
    format_version = manifest["format"]
    entries = manifest.get("entries")
    if not isinstance(entries, dict):
        raise ValueError("EEF manifest entries are invalid")
    expected_names = set(entries) | {"manifest.json", "attestation.json"}
    if set(blobs) != expected_names:
        raise ValueError("EEF contains unsigned or missing entries")
    if "reproduce.json" not in entries or "Dockerfile" not in entries:
        raise ValueError("EEF claim payload is incomplete")
    for name, metadata in entries.items():
        if not isinstance(name, str) or not isinstance(metadata, dict):
            raise ValueError("EEF manifest entry metadata is invalid")
        _safe_relative(name)
        size = metadata.get("size")
        digest = metadata.get("sha256")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not _is_sha256(digest)
        ):
            raise ValueError(f"EEF manifest entry metadata is invalid: {name}")
        content = blobs.get(name)
        if content is None or len(content) != size:
            raise ValueError(f"EEF entry size mismatch: {name}")
        if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), digest):
            raise ValueError(f"EEF entry hash mismatch: {name}")

    verified_identity = None
    if format_version == PUBLIC_FORMAT_VERSION:
        if signing_key is not None or trust_root is None or trust_anchor is None:
            raise ValueError("EEF v4 verification requires only a trust root and anchor")
        statement, verified_identity = verify_envelope(
            blobs["attestation.json"],
            trust_root=trust_root,
            trust_anchor=trust_anchor,
            purpose="eef",
            evaluated_at=evaluated_at,
        )
        signature_algorithm = "ed25519"
        signature_value = ",".join(verified_identity.verified_key_ids)
    else:
        if signing_key is None or trust_root is not None or trust_anchor is not None:
            raise ValueError("legacy EEF verification requires only its shared signing key")
        try:
            attestation = json.loads(blobs["attestation.json"])
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("EEF attestation is invalid") from exc
        if not isinstance(attestation, dict):
            raise ValueError("EEF attestation is invalid")
        statement = attestation.get("statement")
        signature = attestation.get("signature", {})
        if (
            not isinstance(statement, dict)
            or not isinstance(signature, dict)
            or signature.get("algorithm") != "hmac-sha256"
        ):
            raise ValueError("EEF attestation is invalid")
        expected_signature = hmac.new(
            signing_key, _canonical(statement), hashlib.sha256
        ).hexdigest()
        signature_value = signature.get("value")
        if not _is_sha256(signature_value) or not hmac.compare_digest(
            expected_signature, signature_value
        ):
            raise ValueError("EEF signature verification failed")
        signature_algorithm = "hmac-sha256"
    if statement.get("_type") != STATEMENT_TYPE or statement.get(
        "predicateType"
    ) != _predicate_type(format_version):
        raise ValueError("EEF attestation is invalid")
    subjects = statement.get("subject")
    if not isinstance(subjects, list) or len(subjects) != 1 or not isinstance(subjects[0], dict):
        raise ValueError("EEF attestation subject is invalid")
    subject = subjects[0]
    digest = subject.get("digest")
    if subject.get("name") != "manifest.json" or not isinstance(digest, dict):
        raise ValueError("EEF attestation subject is invalid")
    subject_digest = digest.get("sha256")
    if not _is_sha256(subject_digest):
        raise ValueError("EEF attestation subject is invalid")
    if not hmac.compare_digest(
        subject_digest or "", hashlib.sha256(blobs["manifest.json"]).hexdigest()
    ):
        raise ValueError("EEF attestation does not cover its manifest")
    predicate = statement.get("predicate")
    if not isinstance(predicate, dict):
        raise ValueError("EEF attestation predicate is invalid")
    claim_type = (
        manifest.get("claim_type") if format_version != _LEGACY_FORMAT_VERSION else "bug_flip"
    )
    if format_version != _LEGACY_FORMAT_VERSION and predicate.get("claim_type") != claim_type:
        raise ValueError("EEF signed claim type is inconsistent")
    has_case = "case.json" in entries
    has_refactor = "refactor.json" in entries
    if has_case == has_refactor:
        raise ValueError("EEF must contain exactly one claim payload")
    receipt_archive = _validated_receipt_archive(
        blobs,
        manifest,
        predicate,
        format_version=format_version,
        claim_type=claim_type,
    )
    if claim_type == "bug_flip" and has_case:
        _validate_bug_bundle(
            blobs,
            manifest,
            statement,
            format_version=format_version,
        )
        execution_verified = (
            _reexecute_bug(blobs, format_version=format_version, docker_bin=docker_bin)
            if execute
            else None
        )
    elif claim_type == "behavior_preserving_refactor" and has_refactor:
        from .eef_refactor import reexecute_refactor, validate_refactor_bundle

        validated = validate_refactor_bundle(
            blobs,
            manifest,
            statement,
            format_version=format_version,
        )
        execution_verified = (
            reexecute_refactor(
                blobs,
                validated,
                format_version=format_version,
                docker_bin=docker_bin,
            )
            if execute
            else None
        )
    else:
        raise ValueError("EEF claim type and payload are unsupported")
    claim_name = "case.json" if has_case else "refactor.json"
    claim = json.loads(blobs[claim_name])
    if not isinstance(claim, dict):
        raise TypeError("EEF verified claim payload was not an object")
    if claim_type == "bug_flip" and receipt_archive is not None:
        validate_release_evidence(
            claim,
            receipt_archive.receipts if receipt_archive else (),
        )
    return VerifiedClaim(
        verification=VerificationResult(True, True, execution_verified),
        format_version=format_version,
        claim_type=claim_type,
        claim=claim,
        manifest_sha256=hashlib.sha256(blobs["manifest.json"]).hexdigest(),
        signature_algorithm=signature_algorithm,
        signature_value=signature_value,
        archived_states=tuple(
            state
            for state in ("base", "target")
            if any(name.startswith(f"sources/{state}/") for name in blobs)
        ),
        connector_receipts=receipt_archive.receipts if receipt_archive else (),
        verified_identity=verified_identity,
    )


def _reexecute_bug(blobs: dict[str, bytes], *, format_version: str, docker_bin: str) -> bool:
    reproduce = json.loads(blobs["reproduce.json"])
    case = json.loads(blobs["case.json"])
    if not isinstance(reproduce, dict) or not isinstance(case, dict):
        raise ValueError("EEF claim payload is invalid")
    reruns = _bounded_int(reproduce.get("reruns"), "reruns", 1, _MAX_RERUNS)
    test = case.get("test_file")
    if not isinstance(test, dict) or not isinstance(test.get("path"), str):
        raise ValueError("EEF Case test artifact is invalid")
    run_argv = reproduce.get("command_argv")
    if not isinstance(run_argv, list) or not all(isinstance(item, str) for item in run_argv):
        raise ValueError("EEF replay argv is invalid")
    validated_argv = _safe_pytest_argv(shlex.join(run_argv), str(test["path"]))
    if validated_argv != run_argv:
        raise ValueError("EEF replay argv is noncanonical")
    environment = _environment_from_bundle(blobs, format_version)
    expected_dockerfile = _dockerfile(validated_argv, environment).encode()
    if not hmac.compare_digest(blobs.get("Dockerfile", b""), expected_dockerfile):
        raise ValueError("EEF Dockerfile does not match the trusted replay harness")
    if environment is not None:
        inspect_local_replay_image(environment, docker_bin=docker_bin)
    with tempfile.TemporaryDirectory(prefix="exhibit-a-eef-") as tmp:
        root = Path(tmp)
        for name, content in blobs.items():
            destination = root.joinpath(*_safe_relative(name).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        digest = hashlib.sha256(blobs["manifest.json"]).hexdigest()
        namespace = uuid.uuid4().hex[:12]
        target_image = f"exhibit-a-eef:{digest}-{namespace}-target"
        base_image = f"exhibit-a-eef:{digest}-{namespace}-base"
        built_images: list[str] = []
        try:
            built_images.append(target_image)
            _build_state(docker_bin, root, "target", target_image, environment=environment)
            target_runs = [
                _run_state(docker_bin, target_image, validated_argv) for _ in range(reruns)
            ]
            base_run = None
            if any(name.startswith("sources/base/") for name in blobs):
                built_images.append(base_image)
                _build_state(docker_bin, root, "base", base_image, environment=environment)
                base_run = _run_state(docker_bin, base_image, validated_argv)
            expected_verdict = normalize_verdict(reproduce.get("verdict"))
            if expected_verdict not in (Verdict.VERIFIED, Verdict.PARTIAL):
                raise ValueError("EEF execution requires a VERIFIED or PARTIAL Case")
            flip = flip_check(
                target_runs=target_runs,
                base_run=base_run,
                test_code=str(case["test_file"]["code"]),
                expected_signature=reproduce.get("expected_signature"),
                allow_reproduced=expected_verdict is Verdict.PARTIAL,
            )
            expected_tier = "flip" if expected_verdict is Verdict.VERIFIED else "reproduced"
            return flip.admissible and flip.tier == expected_tier
        finally:
            for image in reversed(built_images):
                _remove_image(docker_bin, image)


def _validate_bug_bundle(
    blobs: dict[str, bytes],
    manifest: dict[str, Any],
    statement: dict[str, Any],
    *,
    format_version: str,
) -> None:
    try:
        case = json.loads(blobs["case.json"])
        reproduce = json.loads(blobs["reproduce.json"])
    except KeyError as exc:
        raise ValueError(f"EEF is missing required claim entry: {exc.args[0]}") from exc
    if not isinstance(case, dict) or not isinstance(reproduce, dict):
        raise ValueError("EEF claim payload is invalid")
    test = case.get("test_file")
    if not isinstance(test, dict) or not isinstance(test.get("path"), str):
        raise ValueError("EEF Case test artifact is invalid")
    test_path = _safe_relative(test["path"])
    if not isinstance(test.get("code"), str):
        raise ValueError("EEF Case test code is invalid")
    test_code = test["code"].encode()
    target_test = f"sources/target/{test_path.as_posix()}"
    if blobs.get(target_test) != test_code:
        raise ValueError("EEF target snapshot test does not match the Case artifact")
    if any(name.startswith("sources/base/") for name in blobs):
        base_test = f"sources/base/{test_path.as_posix()}"
        if blobs.get(base_test) != test_code:
            raise ValueError("EEF base snapshot test does not match the Case artifact")
    run_argv = reproduce.get("command_argv")
    if not isinstance(run_argv, list) or not all(isinstance(item, str) for item in run_argv):
        raise ValueError("EEF replay argv is invalid")
    validated_argv = _safe_pytest_argv(shlex.join(run_argv), test_path.as_posix())
    if validated_argv != run_argv:
        raise ValueError("EEF replay argv is noncanonical")
    case_command = case.get("run_command")
    if not isinstance(case_command, str):
        raise ValueError("EEF Case run command is invalid")
    if _safe_pytest_argv(case_command, test_path.as_posix()) != run_argv:
        raise ValueError("EEF replay argv does not match the Case command")
    evidence = case.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("EEF Case evidence is invalid")
    reruns = _bounded_int(reproduce.get("reruns"), "reruns", 1, _MAX_RERUNS)
    if _bounded_int(evidence.get("reruns"), "Case reruns", 1, _MAX_RERUNS) != reruns:
        raise ValueError("EEF replay reruns do not match the Case evidence")
    expected_signature = reproduce.get("expected_signature")
    case_signature = evidence.get("fail_signature")
    if expected_signature is not None and not isinstance(expected_signature, str):
        raise ValueError("EEF expected signature is invalid")
    if case_signature is not None and not isinstance(case_signature, str):
        raise ValueError("EEF Case failure signature is invalid")
    if expected_signature != case_signature:
        raise ValueError("EEF replay signature does not match the Case evidence")
    environment = _environment_from_bundle(blobs, format_version)
    expected_dockerfile = _dockerfile(validated_argv, environment).encode()
    if not hmac.compare_digest(blobs.get("Dockerfile", b""), expected_dockerfile):
        raise ValueError("EEF Dockerfile does not match the trusted replay harness")
    try:
        case_verdict = normalize_verdict(case.get("verdict"))
        reproduce_verdict = normalize_verdict(reproduce.get("verdict"))
    except (TypeError, ValueError) as exc:
        raise ValueError("EEF claim verdict metadata is invalid") from exc
    if case_verdict is not reproduce_verdict:
        raise ValueError("EEF claim verdict metadata is inconsistent")
    predicate = statement.get("predicate")
    expected_manifest_keys = {"format", "case_id", "entries"}
    expected_predicate = {
        "case_id": case.get("id"),
        "verdict": case_verdict.value,
        "created_at": case.get("created_at"),
    }
    if format_version != _LEGACY_FORMAT_VERSION:
        expected_manifest_keys.add("claim_type")
        expected_predicate["claim_type"] = "bug_flip"
    if RECEIPT_ENTRY in blobs:
        expected_manifest_keys.update(_receipt_metadata_keys())
        expected_predicate.update(_receipt_metadata(manifest))
    if format_version == PUBLIC_FORMAT_VERSION:
        expected_predicate.update(
            {
                "claimType": "bug_flip",
                "format": PUBLIC_FORMAT_VERSION,
                "publisher": statement["predicate"].get("publisher"),
                "signatureProfile": EEF_PROFILE,
            }
        )
    if (
        set(manifest) != expected_manifest_keys
        or not isinstance(predicate, dict)
        or predicate != expected_predicate
        or manifest.get("case_id") != case.get("id")
        or predicate.get("case_id") != case.get("id")
    ):
        raise ValueError("EEF signed claim metadata is inconsistent")
    fixed_entries = {
        "case.json",
        "reproduce.json",
        "Dockerfile",
        "logs/fail_log.txt",
        "logs/pass_log.txt",
        "logs/control_log.txt",
        "logs/bisect_log.txt",
        "logs/existing_suite_log.txt",
        "manifest.json",
        "attestation.json",
    }
    if format_version == PUBLIC_FORMAT_VERSION:
        fixed_entries.add(REPLAY_ENVIRONMENT_ENTRY)
    if RECEIPT_ENTRY in blobs:
        fixed_entries.add(RECEIPT_ENTRY)
    for name in blobs:
        if name in fixed_entries or name.startswith(("sources/base/", "sources/target/")):
            continue
        raise ValueError(f"EEF contains an unsupported claim entry: {name}")
    expected_logs = {
        "logs/fail_log.txt": evidence.get("fail_log", ""),
        "logs/pass_log.txt": evidence.get("pass_log", ""),
        "logs/control_log.txt": evidence.get("control_log", ""),
        "logs/bisect_log.txt": evidence.get("bisect_log", ""),
        "logs/existing_suite_log.txt": case.get("existing_suite_log", ""),
    }
    for name, value in expected_logs.items():
        if blobs.get(name) != str(value).encode():
            raise ValueError(f"EEF log payload does not match the Case: {name}")


def _build_state(
    docker_bin: str,
    root: Path,
    state: str,
    image: str,
    *,
    environment: ReplayEnvironment | None = None,
) -> None:
    command = [
        docker_bin,
        "build",
        "--network",
        "none",
        "--pull=false",
    ]
    if environment is not None:
        command.extend(["--platform", environment.platform])
    command.extend(
        [
            "--build-arg",
            f"STATE={state}",
            "--tag",
            image,
            "--file",
            str(root / "Dockerfile"),
            str(root),
        ]
    )
    proc, timed_out = _run_process_capped(command, timeout_s=_BUILD_TIMEOUT_S)
    if timed_out:
        raise RuntimeError(f"offline EEF image build timed out for {state}")
    if proc.returncode != 0:
        raise RuntimeError(f"offline EEF image build failed for {state}: {proc.stderr.strip()}")


def _run_state(
    docker_bin: str,
    image: str,
    argv: list[str],
    *,
    timeout_s: int = _RUN_TIMEOUT_S,
    output_limit_bytes: int | None = None,
) -> ExecOutcome:
    container_name = f"exhibit-a-eef-{uuid.uuid4().hex}"
    command = [
        docker_bin,
        "run",
        "--rm",
        "--name",
        container_name,
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=128m",
        "--pids-limit",
        "512",
        "--memory",
        "2g",
        "--cpus",
        "2",
        image,
        *argv,
    ]
    try:
        proc, timed_out = _run_process_capped(
            command,
            timeout_s=timeout_s,
            output_limit_bytes=output_limit_bytes,
        )
    finally:
        _remove_container(docker_bin, container_name)
    if timed_out:
        return ExecOutcome(
            124,
            "",
            "TIMEOUT: EEF replay exceeded wall-clock budget",
            timed_out=True,
            duration_s=float(timeout_s),
        )
    return ExecOutcome(proc.returncode, proc.stdout, proc.stderr)


def _run_process_capped(
    argv: list[str], *, timeout_s: int, output_limit_bytes: int | None = None
) -> tuple[subprocess.CompletedProcess[str], bool]:
    output_limit = _OUTPUT_LIMIT_BYTES if output_limit_bytes is None else output_limit_bytes
    if (
        not isinstance(output_limit, int)
        or isinstance(output_limit, bool)
        or output_limit < 1
        or output_limit > _OUTPUT_LIMIT_BYTES
    ):
        raise ValueError("EEF process output limit is invalid")
    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout = bytearray()
    stderr = bytearray()
    truncated = [False, False]

    def drain(stream, destination: bytearray, index: int) -> None:
        while chunk := stream.read(64 * 1024):
            remaining = output_limit - len(destination)
            if remaining > 0:
                destination.extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated[index] = True

    assert process.stdout is not None and process.stderr is not None
    threads = [
        threading.Thread(target=drain, args=(process.stdout, stdout, 0), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr, 1), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        return_code = process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        return_code = process.wait()
    for thread in threads:
        thread.join()

    def render(content: bytearray, was_truncated: bool) -> str:
        text = bytes(content).decode(errors="replace")
        return text + ("\n[EEF output truncated]" if was_truncated else "")

    return (
        subprocess.CompletedProcess(
            argv,
            return_code,
            render(stdout, truncated[0]),
            render(stderr, truncated[1]),
        ),
        timed_out,
    )


def _remove_container(docker_bin: str, name: str) -> None:
    try:
        subprocess.run(
            [docker_bin, "rm", "--force", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_CLEANUP_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _remove_image(docker_bin: str, image: str) -> None:
    try:
        subprocess.run(
            [docker_bin, "image", "rm", "--force", image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_CLEANUP_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _add_source(
    payloads: dict[str, bytes],
    source: Path,
    state: str,
    test_path: PurePosixPath,
    test_code: str,
) -> None:
    for relative, content in _read_source_snapshot(source).items():
        name = f"sources/{state}/{relative.as_posix()}"
        _safe_relative(name)
        payloads[name] = content
    payloads[f"sources/{state}/{test_path.as_posix()}"] = test_code.encode()


def materialize_source_snapshot(
    source: str | Path,
    destination: str | Path,
    *,
    budget: SourceSnapshotBudget | None = None,
) -> Path:
    """Copy one source tree through EEF's race-safe, no-symlink reader."""
    target_root = Path(destination)
    if target_root.exists():
        raise ValueError(f"EEF snapshot destination already exists: {target_root}")
    target_root.mkdir(parents=True)
    for relative, content in _read_source_snapshot(Path(source), budget=budget).items():
        target = target_root.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return target_root


def _read_source_snapshot(
    source: Path, *, budget: SourceSnapshotBudget | None = None
) -> dict[PurePosixPath, bytes]:
    root = source.absolute()
    root_descriptor = _open_directory_no_follow(root)
    files: dict[PurePosixPath, bytes] = {}
    try:
        if not stat.S_ISDIR(os.fstat(root_descriptor).st_mode):
            raise ValueError(f"EEF source snapshot is not a directory: {source}")
        for relative in _discover_source_files(root_descriptor):
            content = _read_source_file(root_descriptor, relative)
            if budget is not None:
                budget.consume(len(content))
            files[PurePosixPath(relative.as_posix())] = content
    finally:
        os.close(root_descriptor)
    return files


def _discover_source_files(root_descriptor: int) -> list[Path]:
    """Stream a bounded, no-symlink tree walk before sorting its file paths."""
    files: list[Path] = []
    visited = 0

    def walk(directory_descriptor: int, parent: Path) -> None:
        nonlocal visited
        try:
            entries = os.scandir(directory_descriptor)
            with entries:
                for entry in entries:
                    if entry.name in _EXCLUDED_PARTS:
                        continue
                    visited += 1
                    if visited > _MAX_ARCHIVE_ENTRIES:
                        raise ValueError("EEF source snapshots contain too many entries")
                    relative = parent / entry.name
                    _safe_relative(relative.as_posix())
                    try:
                        if entry.is_symlink():
                            raise ValueError(
                                f"EEF source snapshots cannot contain symlinks: {relative}"
                            )
                        if entry.is_dir(follow_symlinks=False):
                            child = os.open(
                                entry.name,
                                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory_descriptor,
                            )
                            try:
                                walk(child, relative)
                            finally:
                                os.close(child)
                        elif entry.is_file(follow_symlinks=False):
                            files.append(relative)
                        else:
                            raise ValueError(
                                f"EEF source snapshot entry is not a regular file: {relative}"
                            )
                    except OSError as exc:
                        raise ValueError(
                            f"EEF source snapshot changed during traversal: {relative}"
                        ) from exc
        except OSError as exc:
            raise ValueError("EEF source snapshot could not be traversed safely") from exc

    walk(root_descriptor, Path())
    return sorted(files)


def _dockerfile(argv: list[str], environment: ReplayEnvironment | None = None) -> str:
    if environment is not None:
        return (
            f"FROM {environment.image}\n"
            "ARG STATE\n"
            "WORKDIR /work\n"
            "COPY sources/${STATE}/ /work/\n"
            "USER 65534:65534\n"
            f"CMD {json.dumps(argv, separators=(',', ':'))}\n"
        )
    return (
        "FROM python:3.12-slim\n"
        "RUN python -m pip install --disable-pip-version-check --no-cache-dir "
        f"pytest=={PINNED_PYTEST_VERSION}\n"
        "ARG STATE\n"
        "WORKDIR /work\n"
        "COPY sources/${STATE}/ /work/\n"
        "USER 65534:65534\n"
        f"CMD {json.dumps(argv, separators=(',', ':'))}\n"
    )


def _select_replay_environment(
    private_key_seed: bytes | None,
    value: Mapping[str, Any] | None,
) -> ReplayEnvironment | None:
    if private_key_seed is None:
        if value is not None:
            raise ValueError("legacy EEF cannot carry a v4 replay environment")
        return None
    if value is None:
        raise ValueError("EEF v4 requires an immutable replay environment descriptor")
    return parse_replay_environment(value)


def _environment_from_bundle(
    blobs: Mapping[str, bytes], format_version: str
) -> ReplayEnvironment | None:
    content = blobs.get(REPLAY_ENVIRONMENT_ENTRY)
    if format_version != PUBLIC_FORMAT_VERSION:
        if content is not None:
            raise ValueError("legacy EEF contains a v4 replay environment")
        return None
    if content is None:
        raise ValueError("EEF v4 is missing its replay environment descriptor")
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("EEF replay environment is invalid JSON") from exc
    environment = parse_replay_environment(value)
    if content != _canonical(environment.to_dict()) + b"\n":
        raise ValueError("EEF replay environment is noncanonical")
    return environment


def _safe_pytest_argv(command: str, test_path: str) -> list[str]:
    if any(marker in command for marker in (";", "&", "|", ">", "<", "`", "$")):
        raise ValueError("EEF run command contains a shell control character")
    argv = shlex.split(command)
    if (
        len(argv) >= 3
        and PurePosixPath(argv[0]).name in _ALLOWED_PYTHON_BINS
        and argv[1:3] == ["-m", "pytest"]
    ):
        pytest_args = argv[3:]
    elif argv and PurePosixPath(argv[0]).name in _ALLOWED_PYTEST_BINS:
        pytest_args = argv[1:]
    else:
        raise ValueError("EEF run command must invoke pytest directly")
    positional = [arg for arg in pytest_args if not arg.startswith("-")]
    flags = [arg for arg in pytest_args if arg.startswith("-")]
    if positional != [test_path] or any(flag not in _ALLOWED_PYTEST_FLAGS for flag in flags):
        raise ValueError("EEF run command must target only the generated test")
    return argv


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or len(value) > 1024
        or len(path.parts) > 64
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
        or unicodedata.normalize("NFC", value) != value
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"unsafe EEF path: {value!r}")
    return path


def _validate_zip_entry(info: zipfile.ZipInfo) -> None:
    _safe_relative(info.filename)
    if info.is_dir() or info.flag_bits & 0x1:
        raise ValueError(f"EEF entry is not a plain file: {info.filename}")
    if info.compress_type != zipfile.ZIP_STORED:
        raise ValueError(f"EEF entry uses unsupported compression: {info.filename}")
    if info.file_size < 0 or info.file_size > _MAX_ENTRY_BYTES:
        raise ValueError(f"EEF entry exceeds the size limit: {info.filename}")
    if info.create_system == 3:
        file_type = (info.external_attr >> 16) & 0o170000
        if file_type != stat.S_IFREG:
            raise ValueError(f"EEF entry is not a regular file: {info.filename}")


def _reject_parent_collisions(names: list[str]) -> None:
    portable = [unicodedata.normalize("NFC", name).casefold() for name in names]
    if len(portable) != len(set(portable)):
        raise ValueError("EEF contains paths that collide on portable filesystems")
    paths = set(portable)
    for name in names:
        path = PurePosixPath(unicodedata.normalize("NFC", name).casefold())
        if any(parent.as_posix() in paths for parent in path.parents if parent.as_posix() != "."):
            raise ValueError(f"EEF entry collides with a parent file: {name}")


def _validate_payload_limits(payloads: dict[str, bytes]) -> None:
    if len(payloads) > _MAX_ARCHIVE_ENTRIES:
        raise ValueError("EEF contains too many entries")
    _reject_parent_collisions(list(payloads))
    total = 0
    portable: set[str] = set()
    for name, content in payloads.items():
        _safe_relative(name)
        normalized = unicodedata.normalize("NFC", name).casefold()
        if normalized in portable:
            raise ValueError("EEF contains paths that collide on portable filesystems")
        portable.add(normalized)
        if len(content) > _MAX_ENTRY_BYTES:
            raise ValueError(f"EEF entry exceeds the size limit: {name}")
        total += len(content)
        if total > _MAX_ARCHIVE_BYTES:
            raise ValueError("EEF exceeds the total uncompressed size limit")


def _write_bundle(
    payloads: dict[str, bytes],
    output: str | Path,
    signing_key: bytes | None,
    *,
    private_key_seed: bytes | None = None,
    trust_root: bytes | None = None,
    policy_id: str | None = None,
    format_version: str = FORMAT_VERSION,
    manifest_metadata: Mapping[str, Any],
    predicate: Mapping[str, Any],
) -> Path:
    _validate_signing_inputs(signing_key, private_key_seed, trust_root, policy_id)
    public = private_key_seed is not None
    if public:
        format_version = PUBLIC_FORMAT_VERSION
    if format_version not in {FORMAT_VERSION, RECEIPT_FORMAT_VERSION}:
        if format_version != PUBLIC_FORMAT_VERSION:
            raise ValueError("EEF output format is unsupported")
    manifest = {
        "format": format_version,
        **manifest_metadata,
        "entries": {
            name: {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
            for name, content in sorted(payloads.items())
        },
    }
    manifest_bytes = _canonical(manifest) + b"\n"
    statement_predicate = dict(predicate)
    if public:
        assert trust_root is not None and policy_id is not None
        statement_predicate.update(
            {
                "claimType": statement_predicate.get("claim_type"),
                "format": PUBLIC_FORMAT_VERSION,
                "publisher": {"id": policy_publisher(trust_root, policy_id, purpose="eef")},
                "signatureProfile": EEF_PROFILE,
            }
        )
    statement = {
        "_type": STATEMENT_TYPE,
        "subject": [
            {
                "name": "manifest.json",
                "digest": {"sha256": hashlib.sha256(manifest_bytes).hexdigest()},
            }
        ],
        "predicateType": _predicate_type(format_version),
        "predicate": statement_predicate,
    }
    if public:
        assert private_key_seed is not None and trust_root is not None and policy_id is not None
        attestation = sign_envelope(
            statement,
            payload_type=EEF_PAYLOAD_TYPE,
            private_key_seed=private_key_seed,
            trust_root=trust_root,
            policy_id=policy_id,
            purpose="eef",
        )
    else:
        assert signing_key is not None
        attestation = {
            "statement": statement,
            "signature": {
                "algorithm": "hmac-sha256",
                "value": hmac.new(signing_key, _canonical(statement), hashlib.sha256).hexdigest(),
            },
        }
    payloads["manifest.json"] = manifest_bytes
    payloads["attestation.json"] = (
        canonical_json(attestation) if public else _canonical(attestation)
    ) + b"\n"
    _validate_payload_limits(payloads)

    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(payloads.items()):
            info = zipfile.ZipInfo(name, _ZIP_TIME)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return destination


def _validate_signing_inputs(
    signing_key: bytes | None,
    private_key_seed: bytes | None,
    trust_root: bytes | None,
    policy_id: str | None,
) -> None:
    legacy = signing_key is not None
    public = private_key_seed is not None or trust_root is not None or policy_id is not None
    if legacy == public:
        raise ValueError("select exactly one EEF signing profile")
    if legacy:
        if signing_key is None or len(signing_key) < 32:
            raise ValueError("EEF signing key must contain at least 32 bytes")
        return
    if private_key_seed is None or trust_root is None or policy_id is None:
        raise ValueError("EEF v4 signing requires a private key, trust root, and policy ID")
    if len(private_key_seed) != 32:
        raise ValueError("Ed25519 private key seed must contain exactly 32 bytes")


def _validated_receipt_archive(
    blobs: dict[str, bytes],
    manifest: Mapping[str, Any],
    predicate: Mapping[str, Any],
    *,
    format_version: str,
    claim_type: object,
) -> ReceiptArchive | None:
    if format_version not in {RECEIPT_FORMAT_VERSION, PUBLIC_FORMAT_VERSION}:
        if RECEIPT_ENTRY in blobs:
            raise ValueError("legacy EEF contains an unsupported connector receipt section")
        return None
    if format_version == PUBLIC_FORMAT_VERSION and RECEIPT_ENTRY not in blobs:
        if any(manifest.get(name) is not None for name in _receipt_metadata_keys()):
            raise ValueError("EEF v4 receipt metadata has no connector receipt section")
        return None
    if claim_type == "bug_flip":
        claim_name = "case.json"
        try:
            claim = json.loads(blobs[claim_name])
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("EEF connector receipt claim binding is unavailable") from exc
        if not isinstance(claim, dict):
            raise ValueError("EEF connector receipt claim binding is unavailable")
        binding = claim_binding(
            claim_type="bug_flip",
            claim_content=blobs[claim_name],
            repository_source=claim.get("repo"),
            revision=claim.get("target_commit"),
        )
    elif claim_type == "behavior_preserving_refactor":
        from .eef_refactor import refactor_receipt_binding

        binding = refactor_receipt_binding(blobs.get("refactor.json"))
    else:
        raise ValueError("EEF connector receipt claim type is unsupported")
    content = blobs.get(RECEIPT_ENTRY)
    if content is None:
        raise ValueError("EEF v3 is missing its connector receipt section")
    archive = validate_receipt_archive(content, binding)
    validate_receipt_metadata(manifest, predicate, archive)
    return archive


def _receipt_metadata_keys() -> set[str]:
    return {"receipt_schema", "receipt_count", "receipt_root_sha256"}


def _receipt_metadata(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {name: manifest.get(name) for name in _receipt_metadata_keys()}


def _predicate_type(format_version: str) -> str:
    return {
        _LEGACY_FORMAT_VERSION: _LEGACY_PREDICATE_TYPE,
        FORMAT_VERSION: PREDICATE_TYPE,
        RECEIPT_FORMAT_VERSION: RECEIPT_PREDICATE_TYPE,
        PUBLIC_FORMAT_VERSION: EEF_PREDICATE_TYPE,
    }.get(format_version, "")


def _open_directory_no_follow(path: Path) -> int:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if no_follow is None or directory is None:
        raise RuntimeError("EEF snapshot minting requires O_NOFOLLOW and O_DIRECTORY")
    current = os.open(os.sep, os.O_RDONLY | directory)
    try:
        for part in path.parts[1:]:
            following = os.open(
                part,
                os.O_RDONLY | directory | no_follow,
                dir_fd=current,
            )
            os.close(current)
            current = following
    except OSError as exc:
        os.close(current)
        raise ValueError(f"EEF source directory could not be opened safely: {path}") from exc
    return current


def _read_source_file(root_descriptor: int, relative: Path) -> bytes:
    no_follow = os.O_NOFOLLOW
    directory = os.O_DIRECTORY
    parent = os.dup(root_descriptor)
    try:
        for part in relative.parts[:-1]:
            following = os.open(
                part,
                os.O_RDONLY | directory | no_follow,
                dir_fd=parent,
            )
            os.close(parent)
            parent = following
        descriptor = os.open(relative.name, os.O_RDONLY | no_follow, dir_fd=parent)
    except OSError as exc:
        raise ValueError(f"EEF source file could not be opened safely: {relative}") from exc
    finally:
        os.close(parent)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError(f"EEF source snapshot is not a private regular file: {relative}")
        content = stream.read(_MAX_ENTRY_BYTES + 1)
        after = os.fstat(stream.fileno())
    if len(content) > _MAX_ENTRY_BYTES:
        raise ValueError(f"EEF source file exceeds the size limit: {relative}")
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(content) != after.st_size
    ):
        raise ValueError(f"EEF source file changed while it was read: {relative}")
    return content


def _bounded_int(value: object, label: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        raise ValueError(f"EEF {label} must be an integer from {minimum} to {maximum}")
    return value


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
