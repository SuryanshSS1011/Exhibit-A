from __future__ import annotations

import hashlib
import hmac
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from exhibit_a import eef
from exhibit_a.connectors import (
    CICheckRun,
    CIStatus,
    CIStatusConnector,
    ConnectorOutput,
    ConnectorSecurity,
    EvidenceKind,
    EvidenceProvenance,
    Freshness,
    hash_payload,
)
from exhibit_a.eef import create_bundle, create_refactor_bundle, read_verified_claim, verify_bundle
from exhibit_a.eef_receipts import (
    RECEIPT_ENTRY,
    RECEIPT_SCHEMA,
    claim_binding,
    validate_receipt_archive,
)
from exhibit_a.eef_refactor import refactor_receipt_binding
from exhibit_a.executor.base import ExecOutcome, ExecSpec, Executor, RepoState
from exhibit_a.models.case import (
    Case,
    Evidence,
    ExecutionTruth,
    GoalTruth,
    Mode,
    RunResult,
    TargetKind,
    Verdict,
)
from exhibit_a.models.case import TestArtifact as CaseTestArtifact
from exhibit_a.passport import create_passport
from exhibit_a.verdict.refactor_runner import collect_refactor_evidence

KEY = b"eef-v3-receipt-publisher-key-32-bytes"
REVISION = "1f9473f8d6940935ec45a41cb518d9038e0bea0e"
OBSERVED = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
TEST_CODE = (
    "from inventory import stock_for\n\n"
    "def test_unknown_sku():\n"
    "    assert stock_for([], 'missing') == 0\n"
)


class PassingExecutor(Executor):
    source_access = "read_only"
    network_access = "disabled"
    isolation = "container"
    credential_access = "none"

    def prepare(self, repo: RepoState) -> str:
        return f"image:{repo.label}"

    def run(self, repo: RepoState, spec: ExecSpec) -> ExecOutcome:
        return ExecOutcome(0, "1 passed", "")


def _case(
    case_id: str = "eef-v3-fixture",
    repo: str = "https://github.com/example/project.git",
) -> dict:
    case = Case(
        id=case_id,
        mode=Mode.DETECTIVE,
        repo=repo,
        base_commit="a" * 40,
        target_commit=REVISION,
        target_state=TargetKind.SYNTHESIZED_PATCH,
    )
    case.created_at = "2026-09-01T12:00:00+00:00"
    case.verdict = Verdict.VERIFIED
    case.test_file = CaseTestArtifact("test_repro.py", TEST_CODE)
    case.run_command = "python3 -m pytest -x -q test_repro.py"
    failure = "E   AssertionError: wrong value"
    case.evidence = Evidence(
        fail_log=failure,
        fail_signature="AssertionError: wrong value",
        pass_log="1 passed",
        reruns=2,
        deterministic=True,
        runs=[
            RunResult("target", 1, False, failure, "AssertionError: wrong value"),
            RunResult("target", 1, False, failure, "AssertionError: wrong value"),
            RunResult("base", 0, True, "1 passed"),
        ],
    )
    case.truth.execution = ExecutionTruth.COMPLETED
    case.truth.execution_reason = "target and base executions completed"
    case.truth.goal = GoalTruth.VERIFIED
    case.truth.goal_reason = "the generated test produced a deterministic flip"
    return case.to_dict()


def _output(
    *,
    evidence_id: str = "1" * 32,
    repository: str = "example/project",
    revision: str = REVISION,
    source: str = "https://api.github.com/repos/example/project",
) -> ConnectorOutput[CIStatus]:
    status = CIStatus(
        repository=repository,
        revision=revision,
        reported_total=2,
        checks=(
            CICheckRun(
                "engine",
                "completed",
                "success",
                (OBSERVED - timedelta(minutes=3)).isoformat(),
                (OBSERVED - timedelta(minutes=1)).isoformat(),
            ),
            CICheckRun(
                "web",
                "completed",
                "success",
                (OBSERVED - timedelta(minutes=4)).isoformat(),
                (OBSERVED - timedelta(minutes=2)).isoformat(),
            ),
        ),
    )
    request_sha256 = hash_payload(
        {
            "repository": repository,
            "revision": revision,
            "source": source,
        }
    )
    response_sha256 = hash_payload(status.payload())
    provenance = EvidenceProvenance(
        evidence_id=evidence_id,
        connector_id="github_ci_status",
        connector_version="1",
        capability=EvidenceKind.CI_STATUS,
        source=source,
        source_revision=revision,
        observed_at=OBSERVED.isoformat(),
        source_updated_at=(OBSERVED - timedelta(minutes=1)).isoformat(),
        freshness=Freshness.POINT_IN_TIME,
        description="Read read-only CI check-run status for one commit",
        request_sha256=request_sha256,
        response_sha256=response_sha256,
        artifact_sha256="2" * 64,
        content_sha256=hash_payload(
            {
                "request_sha256": request_sha256,
                "response_sha256": response_sha256,
            }
        ),
        security=ConnectorSecurity(
            source_access="read_only",
            network_access="host_unrestricted",
            isolation="in_process",
        ),
    )
    return ConnectorOutput(status, provenance)


def _sources(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    base = tmp_path / "base"
    target = tmp_path / "target"
    base.mkdir()
    target.mkdir()
    (base / "inventory.py").write_text("def stock_for(rows, sku): return 0\n")
    (target / "inventory.py").write_text("def stock_for(rows, sku): return 1\n")
    return base, target


def _bundle(
    tmp_path: Path,
    *,
    case_id: str = "eef-v3-fixture",
    outputs: tuple[ConnectorOutput[CIStatus], ...] | None = None,
    name: str = "case.eef",
) -> Path:
    base, target = _sources(tmp_path)
    return create_bundle(
        _case(case_id),
        tmp_path / name,
        target_source=target,
        base_source=base,
        signing_key=KEY,
        connector_outputs=outputs or (_output(),),
    )


def _resign_bundle(bundle: Path, output: Path, mutate) -> Path:
    with zipfile.ZipFile(bundle) as archive:
        blobs = {name: archive.read(name) for name in archive.namelist()}
    mutate(blobs)
    manifest = json.loads(blobs["manifest.json"])
    attestation = json.loads(blobs["attestation.json"])
    predicate = attestation["statement"]["predicate"]
    if RECEIPT_ENTRY in blobs:
        receipt_document = json.loads(blobs[RECEIPT_ENTRY])
        receipt_metadata = {
            "receipt_schema": receipt_document.get("schema_version"),
            "receipt_count": len(receipt_document.get("receipts", [])),
            "receipt_root_sha256": hashlib.sha256(blobs[RECEIPT_ENTRY]).hexdigest(),
        }
        manifest.update(receipt_metadata)
        predicate.update(receipt_metadata)
    manifest["entries"] = {
        name: {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
        for name, content in sorted(blobs.items())
        if name not in {"manifest.json", "attestation.json"}
    }
    blobs["manifest.json"] = eef._canonical(manifest) + b"\n"
    attestation["statement"]["subject"][0]["digest"]["sha256"] = hashlib.sha256(
        blobs["manifest.json"]
    ).hexdigest()
    attestation["signature"]["value"] = hmac.new(
        KEY,
        eef._canonical(attestation["statement"]),
        hashlib.sha256,
    ).hexdigest()
    blobs["attestation.json"] = eef._canonical(attestation) + b"\n"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(blobs.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return output


def _mutate_receipts(blobs: dict[str, bytes], mutate) -> None:
    document = json.loads(blobs[RECEIPT_ENTRY])
    mutate(document)
    blobs[RECEIPT_ENTRY] = eef._canonical(document) + b"\n"


def _replace_request_digest(document: dict) -> None:
    provenance = document["receipts"][0]["provenance"]
    provenance["request_sha256"] = "3" * 64
    provenance["content_sha256"] = hash_payload(
        {
            "request_sha256": provenance["request_sha256"],
            "response_sha256": provenance["response_sha256"],
        }
    )


def test_v3_receipts_are_deterministic_claim_bound_and_offline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    first = _bundle(tmp_path / "first")
    second = _bundle(tmp_path / "second")

    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        attestation = json.loads(archive.read("attestation.json"))
        document = json.loads(archive.read(RECEIPT_ENTRY))
    assert manifest["format"] == "eef/v3"
    assert manifest["receipt_schema"] == RECEIPT_SCHEMA
    assert manifest["receipt_count"] == 1
    assert attestation["statement"]["predicateType"] == "https://exhibit-a.dev/eef/v3"
    assert document["claim_binding"]["revision"] == REVISION
    assert document["claim_binding"]["repository"] == "example/project"
    assert document["claim_binding"]["repository_origin"] == "https://github.com"
    assert document["receipts"][0]["request"] == {
        "repository": "example/project",
        "revision": REVISION,
        "source": "https://api.github.com/repos/example/project",
    }
    assert document["receipts"][0]["payload"]["checks"][0]["name"] == "engine"

    monkeypatch.setattr(
        CIStatusConnector,
        "collect",
        lambda *args, **kwargs: pytest.fail("offline verification made a network collection"),
    )
    verified = read_verified_claim(first, signing_key=KEY)
    assert verified.format_version == "eef/v3"
    assert len(verified.connector_receipts) == 1
    assert verified.connector_receipts[0]["provenance"]["observed_at"] == OBSERVED.isoformat()
    with pytest.raises(ValueError, match="do not yet support EEF connector receipts"):
        create_passport(first, tmp_path / "unsupported.passport.json", signing_key=KEY)


def test_v2_verification_exposes_no_remote_receipts(tmp_path: Path):
    base, target = _sources(tmp_path)
    bundle = create_bundle(
        _case(),
        tmp_path / "v2.eef",
        target_source=target,
        base_source=base,
        signing_key=KEY,
    )

    verified = read_verified_claim(bundle, signing_key=KEY)

    assert verified.format_version == "eef/v2"
    assert verified.connector_receipts == ()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda document: document["claim_binding"].update(claim_sha256="0" * 64),
            "do not match the archived claim",
        ),
        (
            lambda document: document["receipts"][0]["payload"].update(revision="b" * 40),
            "revision does not match its claim",
        ),
        (
            lambda document: document["receipts"][0]["request"].update(repository="other/project"),
            "request repository does not match its claim",
        ),
        (
            _replace_request_digest,
            "request digest does not cover its request",
        ),
        (
            lambda document: document["receipts"][0]["provenance"].update(
                freshness="immutable_revision"
            ),
            "freshness is invalid",
        ),
        (
            lambda document: document["receipts"][0]["provenance"].update(
                source="https://user:secret@api.github.com/repos/example/project"
            ),
            "source is unsafe or not credential-free",
        ),
        (
            lambda document: document["receipts"][0]["provenance"].update(
                source="http://api.github.com/repos/example/project"
            ),
            "source is unsafe or not credential-free",
        ),
    ],
)
def test_v3_rejects_validly_resigned_semantic_receipt_tampering(
    tmp_path: Path, mutate, message: str
):
    bundle = _bundle(tmp_path / "source")
    tampered = _resign_bundle(
        bundle,
        tmp_path / "tampered.eef",
        lambda blobs: _mutate_receipts(blobs, mutate),
    )

    with pytest.raises(ValueError, match=message):
        verify_bundle(tampered, signing_key=KEY)


def test_v3_rejects_omitted_receipt_section_even_when_resigned(tmp_path: Path):
    bundle = _bundle(tmp_path / "source")

    def omit(blobs: dict[str, bytes]) -> None:
        del blobs[RECEIPT_ENTRY]

    tampered = _resign_bundle(bundle, tmp_path / "omitted.eef", omit)

    with pytest.raises(ValueError, match="missing its connector receipt section"):
        verify_bundle(tampered, signing_key=KEY)


def test_v3_rejects_cross_claim_receipt_substitution_even_when_resigned(tmp_path: Path):
    first = _bundle(tmp_path / "first", case_id="first", name="first.eef")
    second = _bundle(tmp_path / "second", case_id="second", name="second.eef")
    with zipfile.ZipFile(first) as archive:
        substituted_receipts = archive.read(RECEIPT_ENTRY)

    tampered = _resign_bundle(
        second,
        tmp_path / "substituted.eef",
        lambda blobs: blobs.__setitem__(RECEIPT_ENTRY, substituted_receipts),
    )

    with pytest.raises(ValueError, match="do not match the archived claim"):
        verify_bundle(tampered, signing_key=KEY)


def test_v3_rejects_noncanonical_receipt_encoding_even_when_resigned(tmp_path: Path):
    bundle = _bundle(tmp_path / "source")

    def make_noncanonical(blobs: dict[str, bytes]) -> None:
        document = json.loads(blobs[RECEIPT_ENTRY])
        blobs[RECEIPT_ENTRY] = json.dumps(document, indent=2).encode() + b"\n"

    tampered = _resign_bundle(bundle, tmp_path / "noncanonical.eef", make_noncanonical)

    with pytest.raises(ValueError, match="noncanonical"):
        verify_bundle(tampered, signing_key=KEY)


def test_v3_rejects_duplicate_receipt_ids_before_writing(tmp_path: Path):
    base, target = _sources(tmp_path)

    with pytest.raises(ValueError, match="evidence IDs must be unique"):
        create_bundle(
            _case(),
            tmp_path / "duplicate.eef",
            target_source=target,
            base_source=base,
            signing_key=KEY,
            connector_outputs=(_output(), _output()),
        )
    assert not (tmp_path / "duplicate.eef").exists()


def test_v3_rejects_receipts_for_a_different_repository_or_revision(tmp_path: Path):
    base, target = _sources(tmp_path)

    with pytest.raises(ValueError, match="repository does not match"):
        create_bundle(
            _case(),
            tmp_path / "wrong-repository.eef",
            target_source=target,
            base_source=base,
            signing_key=KEY,
            connector_outputs=(_output(repository="other/project"),),
        )
    with pytest.raises(ValueError, match="revision does not match"):
        create_bundle(
            _case(),
            tmp_path / "wrong-revision.eef",
            target_source=target,
            base_source=base,
            signing_key=KEY,
            connector_outputs=(_output(revision="b" * 40),),
        )
    with pytest.raises(ValueError, match="source does not match its repository origin"):
        create_bundle(
            _case(),
            tmp_path / "wrong-source-path.eef",
            target_source=target,
            base_source=base,
            signing_key=KEY,
            connector_outputs=(_output(source="https://api.github.com/repos/other/project"),),
        )
    with pytest.raises(ValueError, match="source does not match its repository origin"):
        create_bundle(
            _case(),
            tmp_path / "wrong-source-origin.eef",
            target_source=target,
            base_source=base,
            signing_key=KEY,
            connector_outputs=(_output(source="https://evil.example/repos/example/project"),),
        )


def test_v3_accepts_numeric_loopback_ipv6_repository_origin(tmp_path: Path):
    base, target = _sources(tmp_path)
    bundle = create_bundle(
        _case(repo="http://[::1]:8080/example/project"),
        tmp_path / "ipv6.eef",
        target_source=target,
        base_source=base,
        signing_key=KEY,
        connector_outputs=(_output(source="http://[::1]:8080/repos/example/project"),),
    )

    with zipfile.ZipFile(bundle) as archive:
        document = json.loads(archive.read(RECEIPT_ENTRY))
    assert document["claim_binding"]["repository_origin"] == "http://[::1]:8080"
    assert verify_bundle(bundle, signing_key=KEY).signature_verified


def test_v3_receipt_parser_bounds_size_and_nesting():
    binding = claim_binding(
        claim_type="bug_flip",
        claim_content=b"{}\n",
        repository_source="https://github.com/example/project",
        revision=REVISION,
    )

    with pytest.raises(ValueError, match="size limit"):
        validate_receipt_archive(b" " * (1024 * 1024 + 1), binding)
    with pytest.raises(ValueError, match="nesting limit"):
        validate_receipt_archive(b"[" * 66 + b"0" + b"]" * 66, binding)


@pytest.mark.parametrize(
    "malformed",
    [
        {
            "runs": [{"state": "target", "evidence_id": []}],
            "evidence_sources": [],
        },
        {
            "runs": [{"state": "target", "evidence_id": "1" * 32}],
            "evidence_sources": [{"evidence_id": []}],
        },
    ],
)
def test_refactor_receipt_binding_rejects_unhashable_target_provenance(malformed: dict):
    with pytest.raises(ValueError, match="target provenance is invalid"):
        refactor_receipt_binding(eef._canonical(malformed))


def test_refactor_claims_support_the_same_v3_receipt_contract(tmp_path: Path):
    base = tmp_path / "base"
    target = tmp_path / "target"
    base.mkdir()
    target.mkdir()
    (base / "behavior.py").write_text("VALUE = 10\n")
    (target / "behavior.py").write_text("VALUE = 10\n")
    evidence = collect_refactor_evidence(
        PassingExecutor(),
        RepoState(
            str(base),
            "base",
            commit="a" * 40,
            source="https://github.com/example/project",
        ),
        RepoState(
            str(target),
            "target",
            commit=REVISION,
            source="https://github.com/example/project",
        ),
        "def test_contract():\n    assert True\n",
    )
    bundle = create_refactor_bundle(
        evidence,
        tmp_path / "refactor.eef",
        base_source=base,
        target_source=target,
        signing_key=KEY,
        connector_outputs=(_output(),),
    )

    verified = read_verified_claim(bundle, signing_key=KEY)

    assert verified.format_version == "eef/v3"
    assert verified.claim_type == "behavior_preserving_refactor"
    assert len(verified.connector_receipts) == 1
    assert verify_bundle(bundle, signing_key=KEY).signature_verified

    def mutate_refactor_claim(blobs: dict[str, bytes]) -> None:
        claim = json.loads(blobs["refactor.json"])
        claim["result"]["reason"] = f"{claim['result']['reason']} changed"
        blobs["refactor.json"] = eef._canonical(claim) + b"\n"

    tampered = _resign_bundle(bundle, tmp_path / "refactor-substitution.eef", mutate_refactor_claim)
    with pytest.raises(ValueError, match="do not match the archived claim"):
        verify_bundle(tampered, signing_key=KEY)
