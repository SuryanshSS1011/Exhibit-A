"""Executable Evidence Format (EEF) deterministic bundle reference implementation."""

from __future__ import annotations

import hashlib
import hmac
import json
import shlex
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .executor.base import ExecOutcome
from .verdict.flip_check import flip_check

FORMAT_VERSION = "eef/v1"
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://exhibit-a.dev/eef/v1"
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_EXCLUDED_PARTS = {".git", ".exhibit-a", "__pycache__", ".env"}


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
    test = case.get("test_file")
    if not isinstance(test, Mapping) or not isinstance(test.get("path"), str):
        raise ValueError("EEF requires a Case with a generated test_file")
    test_path = _safe_relative(str(test["path"]))
    test_code = str(test.get("code", ""))
    run_argv = _safe_pytest_argv(str(case.get("run_command", "")), str(test_path))

    payloads: dict[str, bytes] = {
        "case.json": _canonical(case) + b"\n",
        "reproduce.json": _canonical(
            {
                "command_argv": run_argv,
                "expected_signature": case.get("evidence", {}).get("fail_signature"),
                "reruns": case.get("evidence", {}).get("reruns", 1),
                "verdict": case.get("verdict"),
            }
        )
        + b"\n",
    }
    _add_source(payloads, Path(target_source), "target", test_path, test_code)
    if base_source is not None:
        _add_source(payloads, Path(base_source), "base", test_path, test_code)
    evidence = case.get("evidence", {})
    if isinstance(evidence, Mapping):
        for field in ("fail_log", "pass_log", "control_log", "bisect_log"):
            payloads[f"logs/{field}.txt"] = str(evidence.get(field, "")).encode()
    payloads["logs/existing_suite_log.txt"] = str(case.get("existing_suite_log", "")).encode()
    payloads["Dockerfile"] = _dockerfile(run_argv).encode()

    manifest = {
        "format": FORMAT_VERSION,
        "case_id": case.get("id"),
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
        "predicate": {
            "case_id": case.get("id"),
            "verdict": case.get("verdict"),
            "created_at": case.get("created_at"),
        },
    }
    statement_bytes = _canonical(statement)
    attestation = {
        "statement": statement,
        "signature": {
            "algorithm": "hmac-sha256",
            "value": hmac.new(signing_key, statement_bytes, hashlib.sha256).hexdigest(),
        },
    }
    payloads["manifest.json"] = manifest_bytes
    payloads["attestation.json"] = _canonical(attestation) + b"\n"

    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(payloads.items()):
            info = zipfile.ZipInfo(name, _ZIP_TIME)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return destination


def verify_bundle(
    bundle: str | Path,
    *,
    signing_key: bytes,
    execute: bool = False,
    docker_bin: str = "docker",
) -> VerificationResult:
    """Verify all hashes/signature and optionally re-execute via the flip judge."""
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("EEF contains duplicate paths")
        for name in names:
            _safe_relative(name)
        blobs = {name: archive.read(name) for name in names}
    manifest = json.loads(blobs["manifest.json"])
    expected_names = set(manifest.get("entries", {})) | {"manifest.json", "attestation.json"}
    if set(blobs) != expected_names:
        raise ValueError("EEF contains unsigned or missing entries")
    for name, metadata in manifest.get("entries", {}).items():
        content = blobs.get(name)
        if content is None or len(content) != metadata.get("size"):
            raise ValueError(f"EEF entry size mismatch: {name}")
        if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), metadata.get("sha256", "")):
            raise ValueError(f"EEF entry hash mismatch: {name}")

    attestation = json.loads(blobs["attestation.json"])
    statement = attestation.get("statement")
    signature = attestation.get("signature", {})
    if not isinstance(statement, dict) or signature.get("algorithm") != "hmac-sha256":
        raise ValueError("EEF attestation is invalid")
    subject_digest = statement.get("subject", [{}])[0].get("digest", {}).get("sha256")
    if not hmac.compare_digest(
        subject_digest or "", hashlib.sha256(blobs["manifest.json"]).hexdigest()
    ):
        raise ValueError("EEF attestation does not cover its manifest")
    expected_signature = hmac.new(signing_key, _canonical(statement), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_signature, signature.get("value", "")):
        raise ValueError("EEF signature verification failed")

    execution_verified = _reexecute(blobs, docker_bin=docker_bin) if execute else None
    return VerificationResult(True, True, execution_verified)


def _reexecute(blobs: dict[str, bytes], *, docker_bin: str) -> bool:
    reproduce = json.loads(blobs["reproduce.json"])
    case = json.loads(blobs["case.json"])
    reruns = max(1, int(reproduce.get("reruns", 1)))
    with tempfile.TemporaryDirectory(prefix="exhibit-a-eef-") as tmp:
        root = Path(tmp)
        for name, content in blobs.items():
            destination = root.joinpath(*_safe_relative(name).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        digest = hashlib.sha256(blobs["manifest.json"]).hexdigest()[:16]
        target_image = f"exhibit-a-eef:{digest}-target"
        base_image = f"exhibit-a-eef:{digest}-base"
        _build_state(docker_bin, root, "target", target_image)
        target_runs = [
            _run_state(docker_bin, target_image, list(reproduce["command_argv"]))
            for _ in range(reruns)
        ]
        base_run = None
        if any(name.startswith("sources/base/") for name in blobs):
            _build_state(docker_bin, root, "base", base_image)
            base_run = _run_state(docker_bin, base_image, list(reproduce["command_argv"]))
        flip = flip_check(
            target_runs=target_runs,
            base_run=base_run,
            test_code=str(case["test_file"]["code"]),
            expected_signature=reproduce.get("expected_signature"),
            allow_reproduced=reproduce.get("verdict") == "REPRODUCED",
        )
        expected_tier = "flip" if reproduce.get("verdict") == "PROVEN" else "reproduced"
        return flip.admissible and flip.tier == expected_tier


def _build_state(docker_bin: str, root: Path, state: str, image: str) -> None:
    proc = subprocess.run(
        [
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
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"offline EEF image build failed for {state}: {proc.stderr.strip()}")


def _run_state(docker_bin: str, image: str, argv: list[str]) -> ExecOutcome:
    proc = subprocess.run(
        [
            docker_bin,
            "run",
            "--rm",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=128m",
            image,
            *argv,
        ],
        capture_output=True,
        text=True,
    )
    return ExecOutcome(proc.returncode, proc.stdout, proc.stderr)


def _add_source(
    payloads: dict[str, bytes],
    source: Path,
    state: str,
    test_path: PurePosixPath,
    test_code: str,
) -> None:
    root = source.resolve()
    if not root.is_dir():
        raise ValueError(f"EEF source snapshot is not a directory: {source}")
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in _EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"EEF source snapshots cannot contain symlinks: {relative}")
        if path.is_file():
            payloads[f"sources/{state}/{relative.as_posix()}"] = path.read_bytes()
    payloads[f"sources/{state}/{test_path.as_posix()}"] = test_code.encode()


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
    if len(argv) >= 3 and argv[1:3] == ["-m", "pytest"]:
        pytest_args = argv[3:]
    elif argv and PurePosixPath(argv[0]).name in {"pytest", "pytest3"}:
        pytest_args = argv[1:]
    else:
        raise ValueError("EEF run command must invoke pytest directly")
    positional = [arg for arg in pytest_args if not arg.startswith("-")]
    if positional != [test_path]:
        raise ValueError("EEF run command must target only the generated test")
    return argv


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe EEF path: {value!r}")
    return path


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
