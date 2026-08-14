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
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .verdict.refactor_runner import RefactorEvidence

from .executor.base import ExecOutcome
from .models.case import Verdict, normalize_case_payload, normalize_verdict
from .verdict.flip_check import flip_check

FORMAT_VERSION = "eef/v2"
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://exhibit-a.dev/eef/v2"
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
_ALLOWED_PYTEST_FLAGS = {"-x", "-q", "--tb=short", "--disable-warnings"}
_ALLOWED_PYTHON_BINS = {"python", "python3", "python3.11", "python3.12"}
_ALLOWED_PYTEST_BINS = {"pytest", "pytest3"}


@dataclass(frozen=True)
class VerificationResult:
    integrity_verified: bool
    signature_verified: bool
    execution_verified: bool | None


def create_bundle(
    case: Mapping[str, Any],
    output: str | Path,
    *,
    target_source: str | Path,
    base_source: str | Path | None,
    signing_key: bytes,
) -> Path:
    """Serialize a Case plus source snapshots into a deterministic signed archive."""
    if len(signing_key) < 32:
        raise ValueError("EEF signing key must contain at least 32 bytes")
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
    payloads: dict[str, bytes] = {
        "case.json": _canonical(case) + b"\n",
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
    payloads["Dockerfile"] = _dockerfile(run_argv).encode()

    return _write_bundle(
        payloads,
        output,
        signing_key,
        manifest_metadata={"claim_type": "bug_flip", "case_id": case.get("id")},
        predicate={
            "claim_type": "bug_flip",
            "case_id": case.get("id"),
            "verdict": case.get("verdict"),
            "created_at": case.get("created_at"),
        },
    )


def create_refactor_bundle(
    evidence: RefactorEvidence,
    output: str | Path,
    *,
    base_source: str | Path,
    target_source: str | Path,
    signing_key: bytes,
) -> Path:
    """Serialize behavior-refactor evidence through its claim-specific EEF adapter."""
    from .eef_refactor import create_refactor_bundle as create

    return create(
        evidence,
        output,
        base_source=base_source,
        target_source=target_source,
        signing_key=signing_key,
    )


def verify_bundle(
    bundle: str | Path,
    *,
    signing_key: bytes,
    execute: bool = False,
    docker_bin: str = "docker",
) -> VerificationResult:
    """Verify all hashes/signature and optionally re-execute via the flip judge."""
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
        attestation = json.loads(blobs["attestation.json"])
    except KeyError as exc:
        raise ValueError(f"EEF is missing required entry: {exc.args[0]}") from exc
    if not isinstance(manifest, dict) or manifest.get("format") not in {
        FORMAT_VERSION,
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

    if not isinstance(attestation, dict):
        raise ValueError("EEF attestation is invalid")
    statement = attestation.get("statement")
    signature = attestation.get("signature", {})
    if (
        not isinstance(statement, dict)
        or statement.get("_type") != STATEMENT_TYPE
        or statement.get("predicateType")
        != (PREDICATE_TYPE if format_version == FORMAT_VERSION else _LEGACY_PREDICATE_TYPE)
        or not isinstance(signature, dict)
        or signature.get("algorithm") != "hmac-sha256"
    ):
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
    expected_signature = hmac.new(signing_key, _canonical(statement), hashlib.sha256).hexdigest()
    signature_value = signature.get("value")
    if not _is_sha256(signature_value) or not hmac.compare_digest(
        expected_signature, signature_value
    ):
        raise ValueError("EEF signature verification failed")

    predicate = statement.get("predicate")
    if not isinstance(predicate, dict):
        raise ValueError("EEF attestation predicate is invalid")
    claim_type = manifest.get("claim_type") if format_version == FORMAT_VERSION else "bug_flip"
    if format_version == FORMAT_VERSION and predicate.get("claim_type") != claim_type:
        raise ValueError("EEF signed claim type is inconsistent")
    has_case = "case.json" in entries
    has_refactor = "refactor.json" in entries
    if has_case == has_refactor:
        raise ValueError("EEF must contain exactly one claim payload")
    if claim_type == "bug_flip" and has_case:
        _validate_bug_bundle(
            blobs,
            manifest,
            statement,
            legacy=format_version == _LEGACY_FORMAT_VERSION,
        )
        execution_verified = _reexecute_bug(blobs, docker_bin=docker_bin) if execute else None
    elif claim_type == "behavior_preserving_refactor" and has_refactor:
        from .eef_refactor import reexecute_refactor, validate_refactor_bundle

        validated = validate_refactor_bundle(blobs, manifest, statement)
        execution_verified = (
            reexecute_refactor(blobs, validated, docker_bin=docker_bin) if execute else None
        )
    else:
        raise ValueError("EEF claim type and payload are unsupported")
    return VerificationResult(True, True, execution_verified)


def _reexecute_bug(blobs: dict[str, bytes], *, docker_bin: str) -> bool:
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
    expected_dockerfile = _dockerfile(validated_argv).encode()
    if not hmac.compare_digest(blobs.get("Dockerfile", b""), expected_dockerfile):
        raise ValueError("EEF Dockerfile does not match the trusted replay harness")
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
            _build_state(docker_bin, root, "target", target_image)
            target_runs = [
                _run_state(docker_bin, target_image, validated_argv) for _ in range(reruns)
            ]
            base_run = None
            if any(name.startswith("sources/base/") for name in blobs):
                built_images.append(base_image)
                _build_state(docker_bin, root, "base", base_image)
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
    legacy: bool = False,
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
    expected_dockerfile = _dockerfile(validated_argv).encode()
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
    if not legacy:
        expected_manifest_keys.add("claim_type")
        expected_predicate["claim_type"] = "bug_flip"
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


def _build_state(docker_bin: str, root: Path, state: str, image: str) -> None:
    command = [
        docker_bin,
        "build",
        "--network",
        "none",
        "--build-arg",
        f"STATE={state}",
        "--tag",
        image,
        "--file",
        str(root / "Dockerfile"),
        str(root),
    ]
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
        proc, timed_out = _run_process_capped(command, timeout_s=timeout_s)
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
    argv: list[str], *, timeout_s: int
) -> tuple[subprocess.CompletedProcess[str], bool]:
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
            remaining = _OUTPUT_LIMIT_BYTES - len(destination)
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
    root = source.absolute()
    root_descriptor = _open_directory_no_follow(root)
    try:
        if not stat.S_ISDIR(os.fstat(root_descriptor).st_mode):
            raise ValueError(f"EEF source snapshot is not a directory: {source}")
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if any(part in _EXCLUDED_PARTS for part in relative.parts):
                continue
            if path.is_symlink():
                raise ValueError(f"EEF source snapshots cannot contain symlinks: {relative}")
            if path.is_file():
                name = f"sources/{state}/{relative.as_posix()}"
                _safe_relative(name)
                payloads[name] = _read_source_file(root_descriptor, relative)
        payloads[f"sources/{state}/{test_path.as_posix()}"] = test_code.encode()
    finally:
        os.close(root_descriptor)


def _dockerfile(argv: list[str]) -> str:
    return (
        "FROM python:3.12-slim\n"
        "RUN python -m pip install --disable-pip-version-check --no-cache-dir pytest==8.4.1\n"
        "ARG STATE\n"
        "WORKDIR /work\n"
        "COPY sources/${STATE}/ /work/\n"
        "USER 65534:65534\n"
        f"CMD {json.dumps(argv, separators=(',', ':'))}\n"
    )


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
    signing_key: bytes,
    *,
    manifest_metadata: Mapping[str, Any],
    predicate: Mapping[str, Any],
) -> Path:
    if len(signing_key) < 32:
        raise ValueError("EEF signing key must contain at least 32 bytes")
    manifest = {
        "format": FORMAT_VERSION,
        **manifest_metadata,
        "entries": {
            name: {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
            for name, content in sorted(payloads.items())
        },
    }
    manifest_bytes = _canonical(manifest) + b"\n"
    statement = {
        "_type": STATEMENT_TYPE,
        "subject": [
            {
                "name": "manifest.json",
                "digest": {"sha256": hashlib.sha256(manifest_bytes).hexdigest()},
            }
        ],
        "predicateType": PREDICATE_TYPE,
        "predicate": dict(predicate),
    }
    attestation = {
        "statement": statement,
        "signature": {
            "algorithm": "hmac-sha256",
            "value": hmac.new(signing_key, _canonical(statement), hashlib.sha256).hexdigest(),
        },
    }
    payloads["manifest.json"] = manifest_bytes
    payloads["attestation.json"] = _canonical(attestation) + b"\n"
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
