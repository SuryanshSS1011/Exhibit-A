from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import sys
import zipfile
from dataclasses import asdict
from pathlib import Path

import pytest

import exhibit_a.eef as eef
from exhibit_a.connectors import LocalTestConnector, LocalTestRequest
from exhibit_a.eef import create_bundle, verify_bundle
from exhibit_a.executor.base import ExecOutcome, ExecSpec, Executor, RepoState
from exhibit_a.models.case import (
    Case,
    Evidence,
    Mode,
    ProposalRun,
    Verdict,
)
from exhibit_a.models.case import TestArtifact as CaseTestArtifact

KEY = b"evidence-publisher-test-key-32-bytes!!"
TEST_CODE = (
    "from inventory import stock_for\n\n"
    "def test_unknown_sku():\n"
    "    assert stock_for([], 'missing') == 0\n"
)


def _resign_bundle(bundle: Path, output: Path, mutate) -> Path:
    with zipfile.ZipFile(bundle) as archive:
        blobs = {name: archive.read(name) for name in archive.namelist()}
    mutate(blobs)
    manifest = json.loads(blobs["manifest.json"])
    manifest["entries"] = {
        name: {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
        for name, content in sorted(blobs.items())
        if name not in {"manifest.json", "attestation.json"}
    }
    blobs["manifest.json"] = eef._canonical(manifest) + b"\n"
    attestation = json.loads(blobs["attestation.json"])
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


def _case() -> dict:
    case = Case(id="eef-fixture", mode=Mode.DETECTIVE)
    case.created_at = "2026-07-21T00:00:00+00:00"
    case.verdict = Verdict.VERIFIED
    case.test_file = CaseTestArtifact("test_repro.py", TEST_CODE)
    case.run_command = "python3 -m pytest -x -q test_repro.py"
    case.proposal_runs = [
        ProposalRun(
            operation="propose",
            provider="test-provider",
            requested_model="requested",
            confirmed_model="unknown_no_telemetry",
            confirmed_version="unknown_no_telemetry",
            output_sha256="0" * 64,
        )
    ]
    case.evidence = Evidence(
        fail_log="E   AssertionError: wrong value",
        fail_signature="AssertionError: wrong value",
        pass_log="1 passed",
        reruns=2,
        deterministic=True,
    )
    return case.to_dict()


def test_eef_is_byte_deterministic_and_verifies_offline(tmp_path: Path):
    target = tmp_path / "target"
    base = tmp_path / "base"
    target.mkdir()
    base.mkdir()
    (target / "inventory.py").write_text("def stock_for(rows, sku): return 1\n")
    (base / "inventory.py").write_text("def stock_for(rows, sku): return 0\n")
    (target / ".env").write_text("SECRET=excluded\n")

    first = create_bundle(
        _case(),
        tmp_path / "first.eef",
        target_source=target,
        base_source=base,
        signing_key=KEY,
    )
    second = create_bundle(
        _case(),
        tmp_path / "second.eef",
        target_source=target,
        base_source=base,
        signing_key=KEY,
    )

    assert first.read_bytes() == second.read_bytes()
    result = verify_bundle(first, signing_key=KEY)
    assert result.integrity_verified
    assert result.signature_verified
    assert result.execution_verified is None
    with pytest.raises(ValueError, match="signature verification failed"):
        verify_bundle(first, signing_key=b"different-publisher-key-32-bytes!!!")


def test_eef_verifier_preserves_legacy_v1_bug_bundle_compatibility(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "inventory.py").write_text("def stock_for(rows, sku): return 1\n")
    current = create_bundle(
        _case(),
        tmp_path / "current.eef",
        target_source=target,
        base_source=None,
        signing_key=KEY,
    )

    def make_legacy(blobs: dict[str, bytes]) -> None:
        manifest = json.loads(blobs["manifest.json"])
        manifest["format"] = "eef/v1"
        manifest.pop("claim_type")
        blobs["manifest.json"] = eef._canonical(manifest) + b"\n"
        attestation = json.loads(blobs["attestation.json"])
        attestation["statement"]["predicateType"] = "https://exhibit-a.dev/eef/v1"
        attestation["statement"]["predicate"].pop("claim_type")
        blobs["attestation.json"] = eef._canonical(attestation) + b"\n"

    legacy = _resign_bundle(current, tmp_path / "legacy-v1.eef", make_legacy)
    result = verify_bundle(legacy, signing_key=KEY)
    assert result.integrity_verified and result.signature_verified
    assert result.execution_verified is None


def test_eef_detects_runtime_model_evidence_tampering(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "inventory.py").write_text("def stock_for(rows, sku): return 1\n")
    bundle = create_bundle(
        _case(),
        tmp_path / "case.eef",
        target_source=target,
        base_source=None,
        signing_key=KEY,
    )
    tampered = tmp_path / "tampered.eef"
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(tampered, "w") as destination:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "case.json":
                content = content.replace(
                    b'"requested_model":"requested"', b'"requested_model":"swapped"'
                )
            destination.writestr(info, content)

    with pytest.raises(ValueError, match="entry (size|hash) mismatch"):
        verify_bundle(tampered, signing_key=KEY)


def test_eef_detects_connector_provenance_tampering(tmp_path: Path):
    class StubExecutor(Executor):
        def prepare(self, repo: RepoState) -> None:
            return None

        def run(self, repo: RepoState, spec: ExecSpec) -> ExecOutcome:
            return ExecOutcome(1, "", "E   AssertionError: wrong value")

    target = tmp_path / "target"
    target.mkdir()
    (target / "inventory.py").write_text("def stock_for(rows, sku): return 1\n")
    case = _case()
    request = LocalTestRequest(
        RepoState(str(target), "target"),
        ExecSpec("test_repro.py", TEST_CODE, "python3 -m pytest -x -q test_repro.py"),
    )
    case["evidence_sources"] = [
        asdict(LocalTestConnector(StubExecutor()).collect(request).provenance)
    ]
    bundle = create_bundle(
        case,
        tmp_path / "case.eef",
        target_source=target,
        base_source=None,
        signing_key=KEY,
    )
    tampered = tmp_path / "tampered-connector.eef"
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(tampered, "w") as destination:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "case.json":
                content = content.replace(b'"isolation":"unknown"', b'"isolation":"container"')
            destination.writestr(info, content)

    with pytest.raises(ValueError, match="entry (size|hash) mismatch"):
        verify_bundle(tampered, signing_key=KEY)


def test_eef_canonicalizes_legacy_verdict_metadata(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "inventory.py").write_text("def stock_for(rows, sku): return 1\n")
    case = _case()
    case["verdict"] = "PROVEN"
    case["disposition"] = "REPRODUCED"

    bundle = create_bundle(
        case,
        tmp_path / "legacy.eef",
        target_source=target,
        base_source=None,
        signing_key=KEY,
    )

    with zipfile.ZipFile(bundle) as archive:
        bundled_case = json.loads(archive.read("case.json"))
        reproduce = json.loads(archive.read("reproduce.json"))
        attestation = json.loads(archive.read("attestation.json"))
    assert bundled_case["verdict"] == "VERIFIED"
    assert bundled_case["disposition"] == "PARTIAL"
    assert reproduce["verdict"] == "VERIFIED"
    assert attestation["statement"]["predicate"]["verdict"] == "VERIFIED"


def test_eef_reexecution_uses_docker_argv_and_unchanged_flip_judge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    target = tmp_path / "target"
    base = tmp_path / "base"
    target.mkdir()
    base.mkdir()
    (target / "inventory.py").write_text("def stock_for(rows, sku): return 1\n")
    (base / "inventory.py").write_text("def stock_for(rows, sku): return 0\n")
    bundle = create_bundle(
        _case(),
        tmp_path / "case.eef",
        target_source=target,
        base_source=base,
        signing_key=KEY,
    )
    calls: list[list[str]] = []

    def fake_process(argv: list[str], **kwargs):
        calls.append(argv)
        if argv[1] == "build":
            return subprocess.CompletedProcess(argv, 0, "built", ""), False
        if any("-target" in arg for arg in argv):
            return (
                subprocess.CompletedProcess(argv, 1, "", "E   AssertionError: wrong value"),
                False,
            )
        return subprocess.CompletedProcess(argv, 0, "1 passed", ""), False

    monkeypatch.setattr(eef, "_run_process_capped", fake_process)
    monkeypatch.setattr(
        "exhibit_a.eef.subprocess.run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", ""),
    )

    result = verify_bundle(bundle, signing_key=KEY, execute=True)

    assert result.execution_verified is True
    assert all(isinstance(call, list) for call in calls)
    assert all(
        "--network" in call and call[call.index("--network") + 1] == "none" for call in calls
    )
    assert len([call for call in calls if call[1] == "run"]) == 3
    for call in (call for call in calls if call[1] == "run"):
        assert call[call.index("--pids-limit") + 1] == "512"
        assert call[call.index("--memory") + 1] == "2g"
        assert call[call.index("--cpus") + 1] == "2"


def test_eef_rejects_validly_signed_archive_controlled_dockerfile(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "inventory.py").write_text("def stock_for(rows, sku): return 1\n")
    bundle = create_bundle(
        _case(),
        tmp_path / "case.eef",
        target_source=target,
        base_source=None,
        signing_key=KEY,
    )
    malicious = _resign_bundle(
        bundle,
        tmp_path / "malicious.eef",
        lambda blobs: blobs.__setitem__("Dockerfile", b"FROM scratch\nRUN malicious\n"),
    )

    with pytest.raises(ValueError, match="trusted replay harness"):
        verify_bundle(malicious, signing_key=KEY)


def test_eef_rejects_snapshot_test_that_differs_from_case_artifact(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "inventory.py").write_text("def stock_for(rows, sku): return 1\n")
    bundle = create_bundle(
        _case(),
        tmp_path / "case.eef",
        target_source=target,
        base_source=None,
        signing_key=KEY,
    )
    inconsistent = _resign_bundle(
        bundle,
        tmp_path / "inconsistent-test.eef",
        lambda blobs: blobs.__setitem__(
            "sources/target/test_repro.py", b"def test_other(): assert True\n"
        ),
    )

    with pytest.raises(ValueError, match="snapshot test does not match"):
        verify_bundle(inconsistent, signing_key=KEY)


def test_eef_rejects_log_payload_that_differs_from_case(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "inventory.py").write_text("def stock_for(rows, sku): return 1\n")
    bundle = create_bundle(
        _case(),
        tmp_path / "case.eef",
        target_source=target,
        base_source=None,
        signing_key=KEY,
    )
    inconsistent = _resign_bundle(
        bundle,
        tmp_path / "inconsistent-log.eef",
        lambda blobs: blobs.__setitem__("logs/fail_log.txt", b"different failure"),
    )

    with pytest.raises(ValueError, match="log payload does not match"):
        verify_bundle(inconsistent, signing_key=KEY)


@pytest.mark.parametrize("field", ["signature", "reruns", "argv"])
def test_eef_rejects_replay_metadata_that_weakens_case_claim(tmp_path: Path, field: str):
    target = tmp_path / "target"
    target.mkdir()
    (target / "inventory.py").write_text("def stock_for(rows, sku): return 1\n")
    bundle = create_bundle(
        _case(),
        tmp_path / "case.eef",
        target_source=target,
        base_source=None,
        signing_key=KEY,
    )

    def mutate(blobs):
        reproduce = json.loads(blobs["reproduce.json"])
        if field == "signature":
            reproduce["expected_signature"] = None
        elif field == "reruns":
            reproduce["reruns"] = 1
        else:
            reproduce["command_argv"] = ["python3", "-m", "pytest", "-q", "test_repro.py"]
        blobs["reproduce.json"] = eef._canonical(reproduce) + b"\n"

    inconsistent = _resign_bundle(bundle, tmp_path / f"inconsistent-{field}.eef", mutate)

    with pytest.raises(ValueError, match="match"):
        verify_bundle(inconsistent, signing_key=KEY)


@pytest.mark.parametrize("reruns", [True, 0, 21, "2"])
def test_eef_rejects_invalid_signed_rerun_budgets(tmp_path: Path, reruns):
    target = tmp_path / "target"
    target.mkdir()
    (target / "inventory.py").write_text("def stock_for(rows, sku): return 1\n")
    bundle = create_bundle(
        _case(),
        tmp_path / "case.eef",
        target_source=target,
        base_source=None,
        signing_key=KEY,
    )

    def mutate(blobs):
        reproduce = json.loads(blobs["reproduce.json"])
        reproduce["reruns"] = reruns
        blobs["reproduce.json"] = eef._canonical(reproduce) + b"\n"

    invalid = _resign_bundle(bundle, tmp_path / f"invalid-{reruns!s}.eef", mutate)

    with pytest.raises(ValueError, match="reruns must be an integer"):
        verify_bundle(invalid, signing_key=KEY)


def test_eef_rejects_noncanonical_and_compressed_archive_entries(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "inventory.py").write_text("def stock_for(rows, sku): return 1\n")
    bundle = create_bundle(
        _case(),
        tmp_path / "case.eef",
        target_source=target,
        base_source=None,
        signing_key=KEY,
    )
    noncanonical = tmp_path / "noncanonical.eef"
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(noncanonical, "w") as destination:
        for info in source.infolist():
            destination.writestr(info, source.read(info.filename))
        info = zipfile.ZipInfo("sources//target/extra.py")
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        destination.writestr(info, b"")

    with pytest.raises(ValueError, match="unsafe EEF path"):
        verify_bundle(noncanonical, signing_key=KEY)

    compressed = tmp_path / "compressed.eef"
    with (
        zipfile.ZipFile(bundle) as source,
        zipfile.ZipFile(compressed, "w", compression=zipfile.ZIP_DEFLATED) as destination,
    ):
        for name in source.namelist():
            destination.writestr(name, source.read(name))

    with pytest.raises(ValueError, match="unsupported compression"):
        verify_bundle(compressed, signing_key=KEY)


def test_eef_rejects_casefold_archive_path_collisions(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "inventory.py").write_text("def stock_for(rows, sku): return 1\n")
    bundle = create_bundle(
        _case(),
        tmp_path / "case.eef",
        target_source=target,
        base_source=None,
        signing_key=KEY,
    )
    collision = tmp_path / "collision.eef"
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(collision, "w") as destination:
        for info in source.infolist():
            destination.writestr(info, source.read(info.filename))
        info = zipfile.ZipInfo("sources/target/INVENTORY.py")
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        destination.writestr(info, b"")

    with pytest.raises(ValueError, match="portable filesystems"):
        verify_bundle(collision, signing_key=KEY)


def test_eef_rejects_archive_size_before_reading_payloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    target = tmp_path / "target"
    target.mkdir()
    (target / "inventory.py").write_text("def stock_for(rows, sku): return 1\n")
    bundle = create_bundle(
        _case(),
        tmp_path / "case.eef",
        target_source=target,
        base_source=None,
        signing_key=KEY,
    )
    monkeypatch.setattr(eef, "_MAX_ENTRY_BYTES", 1)

    with pytest.raises(ValueError, match="entry exceeds the size limit"):
        verify_bundle(bundle, signing_key=KEY)


def test_eef_mint_rejects_source_parent_collision_with_generated_test(tmp_path: Path):
    target = tmp_path / "target"
    (target / "test_repro.py").mkdir(parents=True)
    (target / "test_repro.py" / "child").write_text("collision")

    with pytest.raises(ValueError, match="collides with a parent file"):
        create_bundle(
            _case(),
            tmp_path / "case.eef",
            target_source=target,
            base_source=None,
            signing_key=KEY,
        )


def test_eef_mint_rejects_symlinked_source_path_component(tmp_path: Path):
    real_parent = tmp_path / "real"
    target = real_parent / "target"
    target.mkdir(parents=True)
    (target / "inventory.py").write_text("def stock_for(rows, sku): return 1\n")
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(ValueError, match="source directory could not be opened safely"):
        create_bundle(
            _case(),
            tmp_path / "case.eef",
            target_source=linked_parent / "target",
            base_source=None,
            signing_key=KEY,
        )


def test_eef_run_timeout_becomes_failed_reexecution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    target = tmp_path / "target"
    base = tmp_path / "base"
    target.mkdir()
    base.mkdir()
    (target / "inventory.py").write_text("def stock_for(rows, sku): return 1\n")
    (base / "inventory.py").write_text("def stock_for(rows, sku): return 0\n")
    bundle = create_bundle(
        _case(),
        tmp_path / "case.eef",
        target_source=target,
        base_source=base,
        signing_key=KEY,
    )

    def fake_process(argv, **kwargs):
        if argv[1] == "build":
            return subprocess.CompletedProcess(argv, 0, "built", ""), False
        return subprocess.CompletedProcess(argv, -9, "", ""), True

    cleanup_calls: list[list[str]] = []
    monkeypatch.setattr(eef, "_run_process_capped", fake_process)
    monkeypatch.setattr(
        "exhibit_a.eef.subprocess.run",
        lambda argv, **kwargs: (
            cleanup_calls.append(argv) or subprocess.CompletedProcess(argv, 0, "", "")
        ),
    )

    result = verify_bundle(bundle, signing_key=KEY, execute=True)

    assert result.execution_verified is False
    assert any(call[1:3] == ["rm", "--force"] for call in cleanup_calls)
    assert any(call[1:3] == ["image", "rm"] for call in cleanup_calls)


def test_eef_build_timeout_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "inventory.py").write_text("def stock_for(rows, sku): return 1\n")
    bundle = create_bundle(
        _case(),
        tmp_path / "case.eef",
        target_source=target,
        base_source=None,
        signing_key=KEY,
    )

    def fake_process(argv, **kwargs):
        return subprocess.CompletedProcess(argv, -9, "", ""), True

    cleanup_calls: list[list[str]] = []
    monkeypatch.setattr(eef, "_run_process_capped", fake_process)
    monkeypatch.setattr(
        "exhibit_a.eef.subprocess.run",
        lambda argv, **kwargs: (
            cleanup_calls.append(argv) or subprocess.CompletedProcess(argv, 0, "", "")
        ),
    )

    with pytest.raises(RuntimeError, match="image build timed out"):
        verify_bundle(bundle, signing_key=KEY, execute=True)
    assert any(call[1:3] == ["image", "rm"] for call in cleanup_calls)


def test_eef_capped_process_discards_output_beyond_limit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(eef, "_OUTPUT_LIMIT_BYTES", 1024)

    result, timed_out = eef._run_process_capped(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 10000)"],
        timeout_s=5,
    )

    assert not timed_out
    assert result.returncode == 0
    assert len(result.stdout) < 1100
    assert result.stdout.endswith("[EEF output truncated]")
