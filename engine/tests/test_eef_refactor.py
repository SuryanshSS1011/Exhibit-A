from __future__ import annotations

import hashlib
import hmac
import json
import zipfile
from pathlib import Path

import pytest

from exhibit_a import eef, eef_refactor
from exhibit_a.connectors import hash_payload
from exhibit_a.eef import create_refactor_bundle, verify_bundle
from exhibit_a.executor.base import ExecOutcome, ExecSpec, Executor, RepoState
from exhibit_a.models.case import Verdict
from exhibit_a.verdict.refactor_runner import collect_refactor_evidence

KEY = b"refactor-evidence-publisher-key-32-bytes"
CONTRACT = "def test_contract():\n    assert True\n"


class StateExecutor(Executor):
    source_access = "read_only"
    network_access = "disabled"
    isolation = "container"
    credential_access = "none"

    def __init__(self, target: ExecOutcome | None = None):
        self.target = target or ExecOutcome(0, "1 passed", "")

    def prepare(self, repo: RepoState) -> str:
        return f"image:{repo.label}"

    def run(self, repo: RepoState, spec: ExecSpec) -> ExecOutcome:
        if repo.label == "target":
            return self.target
        return ExecOutcome(0, "1 passed", "")


def _sources(tmp_path: Path) -> tuple[Path, Path]:
    base = tmp_path / "base"
    target = tmp_path / "target"
    base.mkdir()
    target.mkdir()
    (base / "behavior.py").write_text("VALUE = 10\n")
    (target / "behavior.py").write_text("VALUE = 10\n")
    return base, target


def _bundle(
    tmp_path: Path,
    *,
    target_outcome: ExecOutcome | None = None,
    name: str = "refactor.eef",
) -> Path:
    base, target = _sources(tmp_path)
    evidence = collect_refactor_evidence(
        StateExecutor(target_outcome),
        RepoState(str(base), "base", commit="a" * 40, source="https://example.com/repo"),
        RepoState(str(target), "target", commit="b" * 40, source="https://example.com/repo"),
        CONTRACT,
    )
    return create_refactor_bundle(
        evidence,
        tmp_path / name,
        base_source=base,
        target_source=target,
        signing_key=KEY,
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
        KEY, eef._canonical(attestation["statement"]), hashlib.sha256
    ).hexdigest()
    blobs["attestation.json"] = eef._canonical(attestation) + b"\n"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(blobs.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return output


def _mutate_json(blobs: dict[str, bytes], name: str, mutate) -> None:
    value = json.loads(blobs[name])
    mutate(value)
    blobs[name] = eef._canonical(value) + b"\n"


def test_refactor_eef_is_deterministic_and_integrity_verifies_without_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    base, target = _sources(tmp_path)
    evidence_object = collect_refactor_evidence(
        StateExecutor(),
        RepoState(str(base), "base", commit="a" * 40, source="https://example.com/repo"),
        RepoState(str(target), "target", commit="b" * 40, source="https://example.com/repo"),
        CONTRACT,
    )
    first = create_refactor_bundle(
        evidence_object,
        tmp_path / "first.eef",
        base_source=base,
        target_source=target,
        signing_key=KEY,
    )
    with zipfile.ZipFile(first) as archive:
        evidence = json.loads(archive.read("refactor.json"))
    second = create_refactor_bundle(
        evidence_object,
        tmp_path / "second.eef",
        base_source=base,
        target_source=target,
        signing_key=KEY,
    )
    assert evidence["result"]["verdict"] == Verdict.VERIFIED.value
    # Re-serializing the same typed evidence is byte-for-byte stable.
    with zipfile.ZipFile(first) as archive:
        blobs = {name: archive.read(name) for name in archive.namelist()}
    assert first.read_bytes() == second.read_bytes()
    monkeypatch.setattr(
        eef_refactor, "_build_state", lambda *args: pytest.fail("unexpected execution")
    )
    result = verify_bundle(first, signing_key=KEY)
    assert result.integrity_verified and result.signature_verified
    assert result.execution_verified is None
    assert set(json.loads(blobs["manifest.json"])) == {
        "format",
        "claim_type",
        "evidence_schema",
        "contract_sha256",
        "entries",
    }


@pytest.mark.parametrize(
    ("target_outcome", "expected_verdict"),
    [
        (ExecOutcome(0, "1 passed", ""), Verdict.VERIFIED),
        (ExecOutcome(1, "", "E   AssertionError: assert 12 == 10"), Verdict.FAILED),
    ],
)
def test_refactor_eef_replay_verifies_the_complete_recorded_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_outcome: ExecOutcome,
    expected_verdict: Verdict,
):
    bundle = _bundle(tmp_path, target_outcome=target_outcome)
    built: list[str] = []
    removed: list[str] = []

    monkeypatch.setattr(
        eef_refactor,
        "_build_state",
        lambda docker, root, state, image: built.append(state),
    )

    def run(docker, image, argv, *, timeout_s):
        if image.endswith("-target"):
            return target_outcome
        return ExecOutcome(0, "1 passed", "")

    monkeypatch.setattr(eef_refactor, "_run_state", run)
    monkeypatch.setattr(eef_refactor, "_remove_image", lambda docker, image: removed.append(image))

    with zipfile.ZipFile(bundle) as archive:
        recorded = json.loads(archive.read("refactor.json"))
    assert recorded["result"]["verdict"] == expected_verdict.value
    result = verify_bundle(bundle, signing_key=KEY, execute=True)
    assert result.execution_verified is True
    assert built == ["base", "target"]
    assert len(removed) == 2


def test_refactor_eef_replay_reports_fresh_result_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    bundle = _bundle(tmp_path)
    monkeypatch.setattr(eef_refactor, "_build_state", lambda *args: None)
    monkeypatch.setattr(
        eef_refactor,
        "_run_state",
        lambda *args, **kwargs: ExecOutcome(1, "", "E   AssertionError: changed"),
    )
    monkeypatch.setattr(eef_refactor, "_remove_image", lambda *args: None)

    assert verify_bundle(bundle, signing_key=KEY, execute=True).execution_verified is False


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda blobs: _mutate_json(
                blobs,
                "refactor.json",
                lambda value: value["runs"][0].update(stdout="forged"),
            ),
            "receipt does not cover",
        ),
        (
            lambda blobs: _mutate_json(
                blobs,
                "refactor.json",
                lambda value: value["result"].update(verdict="FAILED"),
            ),
            "result does not match",
        ),
        (
            lambda blobs: _mutate_json(
                blobs,
                "refactor.json",
                lambda value: value["runs"][1].update(evidence_id=value["runs"][0]["evidence_id"]),
            ),
            "linkage is invalid",
        ),
        (
            lambda blobs: _mutate_json(
                blobs,
                "refactor.json",
                lambda value: value["evidence_sources"][1].update(source_revision="c" * 40),
            ),
            "receipt does not cover|different execution inputs",
        ),
        (
            lambda blobs: blobs.__setitem__("Dockerfile", b"FROM attacker/image\n"),
            "trusted replay harness",
        ),
        (
            lambda blobs: blobs.__setitem__("case.json", b"{}\n"),
            "exactly one claim payload",
        ),
    ],
)
def test_refactor_eef_rejects_signed_semantic_tampering(tmp_path: Path, mutate, message: str):
    bundle = _bundle(tmp_path)
    tampered = _resign_bundle(bundle, tmp_path / "tampered.eef", mutate)

    with pytest.raises(ValueError, match=message):
        verify_bundle(tampered, signing_key=KEY)


def test_refactor_eef_rejects_source_tree_substitution_even_when_resigned(tmp_path: Path):
    bundle = _bundle(tmp_path)

    def mutate(blobs: dict[str, bytes]) -> None:
        blobs["sources/target/behavior.py"] = b"VALUE = 99\n"

    tampered = _resign_bundle(bundle, tmp_path / "substituted.eef", mutate)
    with pytest.raises(ValueError, match="source tree digest mismatch"):
        verify_bundle(tampered, signing_key=KEY)


def test_refactor_eef_rejects_different_images_with_individually_valid_receipts(
    tmp_path: Path,
):
    bundle = _bundle(tmp_path)

    def mutate(blobs: dict[str, bytes]) -> None:
        def change(value: dict) -> None:
            run = value["runs"][1]
            receipt = next(
                item
                for item in value["evidence_sources"]
                if item["evidence_id"] == run["evidence_id"]
            )
            run["image"] = "image:different"
            receipt["request_sha256"] = hash_payload(
                {
                    "command": value["command"],
                    "image": run["image"],
                    "network": False,
                    "repo_revision": receipt["source_revision"],
                    "state": run["state"],
                    "test_code": value["contract_code"],
                    "test_path": value["contract_path"],
                    "timeout_s": value["timeout_s"],
                }
            )
            receipt["content_sha256"] = hash_payload(
                {
                    "request_sha256": receipt["request_sha256"],
                    "response_sha256": receipt["response_sha256"],
                }
            )

        _mutate_json(blobs, "refactor.json", change)

    tampered = _resign_bundle(bundle, tmp_path / "different-image.eef", mutate)
    with pytest.raises(ValueError, match="different execution inputs"):
        verify_bundle(tampered, signing_key=KEY)


def test_refactor_eef_rejects_signed_claim_metadata_disagreement(tmp_path: Path):
    bundle = _bundle(tmp_path)

    def mutate(blobs: dict[str, bytes]) -> None:
        _mutate_json(
            blobs,
            "manifest.json",
            lambda value: value.update(claim_type="bug_flip"),
        )

    tampered = _resign_bundle(bundle, tmp_path / "wrong-claim.eef", mutate)
    with pytest.raises(ValueError, match="claim type is inconsistent"):
        verify_bundle(tampered, signing_key=KEY)


def test_refactor_eef_rejects_ambiguous_extra_manifest_truth(tmp_path: Path):
    bundle = _bundle(
        tmp_path,
        target_outcome=ExecOutcome(1, "", "E   AssertionError: assert 12 == 10"),
    )

    def mutate(blobs: dict[str, bytes]) -> None:
        _mutate_json(
            blobs,
            "manifest.json",
            lambda value: value.update(verdict="VERIFIED"),
        )

    tampered = _resign_bundle(bundle, tmp_path / "ambiguous-manifest.eef", mutate)
    with pytest.raises(ValueError, match="signed claim metadata is inconsistent"):
        verify_bundle(tampered, signing_key=KEY)


def test_refactor_eef_rejects_credential_shaped_executor_handles(tmp_path: Path):
    class UnsafeHandleExecutor(StateExecutor):
        def prepare(self, repo: RepoState) -> str:
            return "https://user:secret@example.com/image"

    base, target = _sources(tmp_path)
    evidence = collect_refactor_evidence(
        UnsafeHandleExecutor(),
        RepoState(str(base), "base"),
        RepoState(str(target), "target"),
        CONTRACT,
    )

    with pytest.raises(ValueError, match="run fields are invalid"):
        create_refactor_bundle(
            evidence,
            tmp_path / "unsafe.eef",
            base_source=base,
            target_source=target,
            signing_key=KEY,
        )
