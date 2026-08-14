"""Claim-specific Executable Evidence Format support for refactor evidence."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .connectors import (
    ConnectorSecurity,
    LocalTestRequest,
    credential_free_source,
    hash_payload,
    local_test_digests,
)
from .eef import (
    _MAX_RERUNS,
    _RUN_TIMEOUT_S,
    _add_source,
    _bounded_int,
    _build_state,
    _canonical,
    _dockerfile,
    _is_sha256,
    _remove_image,
    _run_state,
    _safe_pytest_argv,
    _safe_relative,
    _write_bundle,
)
from .executor.base import ExecOutcome, ExecSpec, RepoState
from .models.case import ExecutionTruth, GoalTruth, ReleaseTruth, Verdict
from .verdict.refactor_check import SuiteStatus, refactor_check
from .verdict.refactor_runner import (
    CLAIM_TYPE,
    CONTRACT_COMMAND,
    CONTRACT_PATH,
    EVIDENCE_SCHEMA,
    RefactorEvidence,
)

_RUN_KEYS = {
    "evidence_id",
    "state",
    "ordinal",
    "exit_code",
    "stdout",
    "stderr",
    "timed_out",
    "duration_s",
    "image",
}
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
_RESULT_KEYS = {
    "verdict",
    "execution",
    "goal",
    "release",
    "reason",
    "deterministic",
    "base",
    "target",
}
_OBSERVATION_KEYS = {
    "status",
    "runs",
    "exit_codes",
    "failure_signature",
    "failure_fingerprint",
    "reason",
}
_EVIDENCE_KEYS = {
    "schema_version",
    "claim_type",
    "contract_path",
    "contract_code",
    "command",
    "contract_sha256",
    "reruns",
    "timeout_s",
    "result",
    "runs",
    "evidence_sources",
}


@dataclass(frozen=True)
class ValidatedRefactorBundle:
    evidence: dict[str, Any]
    base_runs: tuple[ExecOutcome, ...]
    target_runs: tuple[ExecOutcome, ...]
    reruns: int
    timeout_s: int
    argv: tuple[str, ...]


def create_refactor_bundle(
    evidence: RefactorEvidence,
    output: str | Path,
    *,
    base_source: str | Path,
    target_source: str | Path,
    signing_key: bytes,
) -> Path:
    """Serialize typed refactor evidence and both source states into EEF v2."""
    if len(signing_key) < 32:
        raise ValueError("EEF signing key must contain at least 32 bytes")
    if type(evidence) is not RefactorEvidence:
        raise TypeError("refactor EEF requires a RefactorEvidence object")
    # Validate the exact JSON shape readers receive, not dataclass tuple internals.
    data = json.loads(_canonical(evidence.to_dict()))
    validated = _validate_evidence(data)
    test_path = PurePosixPath(CONTRACT_PATH)
    argv = list(validated.argv)
    payloads = {
        "refactor.json": _canonical(data) + b"\n",
    }
    _add_source(payloads, Path(base_source), "base", test_path, evidence.contract_code)
    _add_source(payloads, Path(target_source), "target", test_path, evidence.contract_code)
    reproduce = {
        "claim_type": CLAIM_TYPE,
        "evidence_schema": EVIDENCE_SCHEMA,
        "contract_path": CONTRACT_PATH,
        "command_argv": argv,
        "reruns": evidence.reruns,
        "timeout_s": evidence.timeout_s,
        "base_tree_sha256": _tree_sha256(payloads, "base"),
        "target_tree_sha256": _tree_sha256(payloads, "target"),
    }
    payloads["reproduce.json"] = _canonical(reproduce) + b"\n"
    payloads["Dockerfile"] = _dockerfile(argv).encode()
    result = data["result"]
    return _write_bundle(
        payloads,
        output,
        signing_key,
        manifest_metadata={
            "claim_type": CLAIM_TYPE,
            "evidence_schema": EVIDENCE_SCHEMA,
            "contract_sha256": evidence.contract_sha256,
        },
        predicate={
            "claim_type": CLAIM_TYPE,
            "evidence_schema": EVIDENCE_SCHEMA,
            "contract_sha256": evidence.contract_sha256,
            "verdict": result["verdict"],
            "execution": result["execution"],
            "goal": result["goal"],
            "release": result["release"],
            "deterministic": result["deterministic"],
        },
    )


def validate_refactor_bundle(
    blobs: dict[str, bytes], manifest: dict[str, Any], statement: dict[str, Any]
) -> ValidatedRefactorBundle:
    """Validate signed refactor evidence and re-derive its recorded truth."""
    try:
        evidence = json.loads(blobs["refactor.json"])
        reproduce = json.loads(blobs["reproduce.json"])
    except KeyError as exc:
        raise ValueError(f"refactor EEF is missing required entry: {exc.args[0]}") from exc
    if not isinstance(evidence, dict) or not isinstance(reproduce, dict):
        raise ValueError("refactor EEF claim payload is invalid")
    validated = _validate_evidence(evidence)
    if set(reproduce) != {
        "claim_type",
        "evidence_schema",
        "contract_path",
        "command_argv",
        "reruns",
        "timeout_s",
        "base_tree_sha256",
        "target_tree_sha256",
    }:
        raise ValueError("refactor EEF replay metadata has an invalid shape")
    argv = reproduce.get("command_argv")
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise ValueError("refactor EEF replay argv is invalid")
    if tuple(argv) != validated.argv:
        raise ValueError("refactor EEF replay argv does not match its evidence")
    if (
        reproduce.get("claim_type") != CLAIM_TYPE
        or reproduce.get("evidence_schema") != EVIDENCE_SCHEMA
        or reproduce.get("contract_path") != CONTRACT_PATH
        or reproduce.get("reruns") != validated.reruns
        or reproduce.get("timeout_s") != validated.timeout_s
    ):
        raise ValueError("refactor EEF replay metadata does not match its evidence")
    base_tree_sha = reproduce.get("base_tree_sha256")
    target_tree_sha = reproduce.get("target_tree_sha256")
    if (
        not _is_sha256(base_tree_sha)
        or not _is_sha256(target_tree_sha)
        or not hmac.compare_digest(base_tree_sha, _tree_sha256(blobs, "base"))
        or not hmac.compare_digest(target_tree_sha, _tree_sha256(blobs, "target"))
    ):
        raise ValueError("refactor EEF source tree digest mismatch")
    contract = evidence["contract_code"].encode()
    for state in ("base", "target"):
        if blobs.get(f"sources/{state}/{CONTRACT_PATH}") != contract:
            raise ValueError(f"refactor EEF {state} contract does not match its evidence")
    if blobs.get("Dockerfile") != _dockerfile(argv).encode():
        raise ValueError("refactor EEF Dockerfile does not match the trusted replay harness")
    fixed = {"refactor.json", "reproduce.json", "Dockerfile", "manifest.json", "attestation.json"}
    for name in blobs:
        if name in fixed or name.startswith(("sources/base/", "sources/target/")):
            continue
        raise ValueError(f"refactor EEF contains an unsupported claim entry: {name}")
    predicate = statement.get("predicate")
    result = evidence["result"]
    if (
        set(manifest)
        != {
            "format",
            "claim_type",
            "evidence_schema",
            "contract_sha256",
            "entries",
        }
        or manifest.get("claim_type") != CLAIM_TYPE
        or manifest.get("evidence_schema") != EVIDENCE_SCHEMA
        or manifest.get("contract_sha256") != evidence["contract_sha256"]
        or not isinstance(predicate, dict)
        or predicate
        != {
            "claim_type": CLAIM_TYPE,
            "evidence_schema": EVIDENCE_SCHEMA,
            "contract_sha256": evidence["contract_sha256"],
            "verdict": result["verdict"],
            "execution": result["execution"],
            "goal": result["goal"],
            "release": result["release"],
            "deterministic": result["deterministic"],
        }
    ):
        raise ValueError("refactor EEF signed claim metadata is inconsistent")
    return validated


def reexecute_refactor(
    blobs: dict[str, bytes],
    validated: ValidatedRefactorBundle,
    *,
    docker_bin: str,
) -> bool:
    """Replay both archived states and compare the complete deterministic result."""
    with tempfile.TemporaryDirectory(prefix="exhibit-a-refactor-eef-") as tmp:
        root = Path(tmp)
        for name, content in blobs.items():
            destination = root.joinpath(*_safe_relative(name).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        digest = hashlib.sha256(blobs["manifest.json"]).hexdigest()
        namespace = uuid.uuid4().hex[:12]
        images = {
            state: f"exhibit-a-eef:{digest}-{namespace}-{state}" for state in ("base", "target")
        }
        intended: list[str] = []
        try:
            outcomes: dict[str, list[ExecOutcome]] = {}
            for state in ("base", "target"):
                image = images[state]
                intended.append(image)
                _build_state(docker_bin, root, state, image)
                outcomes[state] = [
                    _run_state(
                        docker_bin,
                        image,
                        list(validated.argv),
                        timeout_s=validated.timeout_s,
                    )
                    for _ in range(validated.reruns)
                ]
            actual = refactor_check(
                base_runs=outcomes["base"],
                target_runs=outcomes["target"],
                required_reruns=validated.reruns,
            )
            return _canonical(asdict(actual)) == _canonical(validated.evidence["result"])
        finally:
            for image in reversed(intended):
                _remove_image(docker_bin, image)


def _validate_evidence(evidence: dict[str, Any]) -> ValidatedRefactorBundle:
    if set(evidence) != _EVIDENCE_KEYS:
        raise ValueError("refactor evidence has an invalid shape")
    if (
        evidence.get("schema_version") != EVIDENCE_SCHEMA
        or evidence.get("claim_type") != CLAIM_TYPE
    ):
        raise ValueError("refactor evidence schema or claim type is unsupported")
    contract_code = evidence.get("contract_code")
    if (
        evidence.get("contract_path") != CONTRACT_PATH
        or evidence.get("command") != CONTRACT_COMMAND
        or not isinstance(contract_code, str)
    ):
        raise ValueError("refactor evidence contract is invalid")
    artifact_sha = hash_payload({"test_code": contract_code, "test_path": CONTRACT_PATH})
    if evidence.get("contract_sha256") != artifact_sha:
        raise ValueError("refactor evidence contract digest mismatch")
    reruns = _bounded_int(evidence.get("reruns"), "refactor reruns", 2, _MAX_RERUNS)
    timeout_s = _bounded_int(evidence.get("timeout_s"), "refactor timeout", 1, _RUN_TIMEOUT_S)
    argv = tuple(_safe_pytest_argv(CONTRACT_COMMAND, CONTRACT_PATH))
    runs = evidence.get("runs")
    sources = evidence.get("evidence_sources")
    if not isinstance(runs, list) or not isinstance(sources, list):
        raise ValueError("refactor evidence runs and receipts must be lists")
    if len(runs) != 2 * reruns or len(sources) != len(runs):
        raise ValueError("refactor evidence must contain one receipt per state run")
    receipts = _validate_receipts(sources)
    if len(receipts) != len(sources):
        raise ValueError("refactor evidence contains duplicate receipt IDs")
    outcomes: dict[str, list[ExecOutcome]] = {"base": [], "target": []}
    state_receipt_identity: dict[str, tuple[object, ...]] = {}
    used: set[str] = set()
    for index, raw in enumerate(runs):
        expected_state = "base" if index < reruns else "target"
        expected_ordinal = index + 1 if expected_state == "base" else index - reruns + 1
        outcome, image, evidence_id = _validate_run(raw, expected_state, expected_ordinal)
        receipt = receipts.get(evidence_id)
        if receipt is None or evidence_id in used:
            raise ValueError("refactor evidence run and receipt linkage is invalid")
        used.add(evidence_id)
        _validate_receipt_for_run(
            receipt,
            outcome,
            image,
            expected_state,
            contract_code,
            timeout_s,
        )
        identity = (
            receipt["source"],
            receipt["source_revision"],
            _canonical(receipt["security"]),
            image,
        )
        prior_identity = state_receipt_identity.setdefault(expected_state, identity)
        if identity != prior_identity:
            raise ValueError("refactor evidence state receipts describe different execution inputs")
        outcomes[expected_state].append(outcome)
    if used != set(receipts):
        raise ValueError("refactor evidence contains an orphan receipt")
    expected_result = evidence.get("result")
    _validate_result_shape(expected_result, reruns)
    actual = refactor_check(
        base_runs=outcomes["base"],
        target_runs=outcomes["target"],
        required_reruns=reruns,
    )
    if _canonical(asdict(actual)) != _canonical(expected_result):
        raise ValueError("refactor evidence result does not match its recorded runs")
    return ValidatedRefactorBundle(
        evidence=evidence,
        base_runs=tuple(outcomes["base"]),
        target_runs=tuple(outcomes["target"]),
        reruns=reruns,
        timeout_s=timeout_s,
        argv=argv,
    )


def _validate_receipts(sources: list[Any]) -> dict[str, dict[str, Any]]:
    receipts: dict[str, dict[str, Any]] = {}
    for receipt in sources:
        if not isinstance(receipt, dict) or set(receipt) != _PROVENANCE_KEYS:
            raise ValueError("refactor evidence receipt has an invalid shape")
        evidence_id = receipt.get("evidence_id")
        security = receipt.get("security")
        if (
            not _is_hex(evidence_id, 32)
            or not isinstance(security, dict)
            or set(security) != _SECURITY_KEYS
        ):
            raise ValueError("refactor evidence receipt identity or security is invalid")
        try:
            ConnectorSecurity(**security)
        except (TypeError, ValueError) as exc:
            raise ValueError("refactor evidence receipt security is invalid") from exc
        if (
            receipt.get("connector_id") != "local_test_runner"
            or receipt.get("connector_version") != "2"
            or receipt.get("capability") != "test_execution"
            or receipt.get("freshness") != "point_in_time"
            or receipt.get("source_updated_at") is not None
            or not isinstance(receipt.get("source"), str)
            or credential_free_source(receipt["source"]) != receipt["source"]
            or receipt.get("source_revision") is not None
            and not isinstance(receipt.get("source_revision"), str)
            or not all(
                _is_sha256(receipt.get(field))
                for field in (
                    "request_sha256",
                    "response_sha256",
                    "artifact_sha256",
                    "content_sha256",
                )
            )
        ):
            raise ValueError("refactor evidence receipt metadata is invalid")
        try:
            observed = datetime.fromisoformat(receipt["observed_at"])
        except (TypeError, ValueError) as exc:
            raise ValueError("refactor evidence receipt observed_at is invalid") from exc
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("refactor evidence receipt observed_at must be timezone-aware")
        if evidence_id in receipts:
            raise ValueError("refactor evidence contains duplicate receipt IDs")
        receipts[evidence_id] = receipt
    return receipts


def _validate_run(
    raw: Any, expected_state: str, expected_ordinal: int
) -> tuple[ExecOutcome, str | None, str]:
    if not isinstance(raw, dict) or set(raw) != _RUN_KEYS:
        raise ValueError("refactor evidence run has an invalid shape")
    evidence_id = raw.get("evidence_id")
    image = raw.get("image")
    duration = raw.get("duration_s")
    if (
        not _is_hex(evidence_id, 32)
        or raw.get("state") != expected_state
        or raw.get("ordinal") != expected_ordinal
        or not isinstance(raw.get("ordinal"), int)
        or isinstance(raw.get("ordinal"), bool)
        or not isinstance(raw.get("exit_code"), int)
        or isinstance(raw.get("exit_code"), bool)
        or not isinstance(raw.get("stdout"), str)
        or not isinstance(raw.get("stderr"), str)
        or not isinstance(raw.get("timed_out"), bool)
        or not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not math.isfinite(duration)
        or duration < 0
        or not _safe_image_handle(image)
    ):
        raise ValueError("refactor evidence run fields are invalid")
    return (
        ExecOutcome(
            raw["exit_code"],
            raw["stdout"],
            raw["stderr"],
            raw["timed_out"],
            duration,
        ),
        image,
        evidence_id,
    )


def _validate_receipt_for_run(
    receipt: dict[str, Any],
    outcome: ExecOutcome,
    image: str | None,
    state: str,
    contract_code: str,
    timeout_s: int,
) -> None:
    description = f"Executed the configured test against the {state} code state"
    if receipt.get("description") != description:
        raise ValueError("refactor evidence receipt state description is invalid")
    request = LocalTestRequest(
        RepoState(
            path="",
            label=state,
            commit=receipt.get("source_revision"),
            source=receipt.get("source"),
        ),
        ExecSpec(
            test_path=CONTRACT_PATH,
            test_code=contract_code,
            command=CONTRACT_COMMAND,
            timeout_s=timeout_s,
            network=False,
            image=image,
        ),
    )
    expected = local_test_digests(request, outcome)
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ValueError("refactor evidence receipt does not cover its linked run")


def _validate_result_shape(result: Any, reruns: int) -> None:
    if not isinstance(result, dict) or set(result) != _RESULT_KEYS:
        raise ValueError("refactor evidence result has an invalid shape")
    try:
        Verdict(result.get("verdict"))
        ExecutionTruth(result.get("execution"))
        GoalTruth(result.get("goal"))
        ReleaseTruth(result.get("release"))
    except (TypeError, ValueError) as exc:
        raise ValueError("refactor evidence result truth values are invalid") from exc
    if not isinstance(result.get("reason"), str) or not isinstance(
        result.get("deterministic"), bool
    ):
        raise ValueError("refactor evidence result fields are invalid")
    for state in ("base", "target"):
        observation = result.get(state)
        if not isinstance(observation, dict) or set(observation) != _OBSERVATION_KEYS:
            raise ValueError("refactor evidence observation has an invalid shape")
        try:
            SuiteStatus(observation.get("status"))
        except (TypeError, ValueError) as exc:
            raise ValueError("refactor evidence observation status is invalid") from exc
        signature = observation.get("failure_signature")
        reason = observation.get("reason")
        if (
            observation.get("runs") != reruns
            or not isinstance(observation.get("runs"), int)
            or isinstance(observation.get("runs"), bool)
            or not _int_list(observation.get("exit_codes"), length=reruns)
            or signature is not None
            and not isinstance(signature, str)
            or reason is not None
            and not isinstance(reason, str)
            or not _string_list(observation.get("failure_fingerprint"))
        ):
            raise ValueError("refactor evidence observation fields are invalid")


def _tree_sha256(payloads: Mapping[str, bytes], state: str) -> str:
    prefix = f"sources/{state}/"
    entries = {
        name.removeprefix(prefix): {
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }
        for name, content in sorted(payloads.items())
        if name.startswith(prefix)
    }
    if not entries:
        raise ValueError(f"refactor EEF is missing the {state} source tree")
    return hashlib.sha256(_canonical(entries)).hexdigest()


def _is_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _safe_image_handle(value: object) -> bool:
    """Allow bounded local image handles, never URLs, paths, or userinfo."""
    if value is None:
        return True
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/-"
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 512
        and value[0] not in "/."
        and "://" not in value
        and "//" not in value
        and all(character in allowed for character in value)
    )


def _int_list(value: object, *, length: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == length
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
    )


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)
