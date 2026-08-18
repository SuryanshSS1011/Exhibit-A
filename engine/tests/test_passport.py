from __future__ import annotations

import hashlib
import hmac
import json
import os
import zipfile
from pathlib import Path

import pytest

from exhibit_a import eef
from exhibit_a import passport as passport_module
from exhibit_a import passport_html as passport_html_module
from exhibit_a.cli import main
from exhibit_a.eef import create_bundle, create_refactor_bundle, read_verified_claim
from exhibit_a.executor.base import ExecOutcome, ExecSpec, Executor, RepoState
from exhibit_a.models.case import (
    Case,
    Evidence,
    ExecutionTruth,
    GoalTruth,
    Mode,
    ProposalRun,
    ReleaseTruth,
    RunResult,
    Verdict,
)
from exhibit_a.models.case import TestArtifact as CaseTestArtifact
from exhibit_a.passport import (
    PASSPORT_SCHEMA,
    create_passport,
    passport_from_verified_claim,
    verify_passport,
)
from exhibit_a.passport_html import create_html_passport, render_html_passport
from exhibit_a.verdict.refactor_runner import collect_refactor_evidence

KEY = b"public-passport-test-key-at-least-32-bytes"
SECRET = "TOP_SECRET_SHOULD_NOT_APPEAR"
PLAIN_SECRET = "sk-proj-PlainCredential123"
PATH_SECRET = "ghp_PathCredential789"


class PassingExecutor(Executor):
    source_access = "disposable_copy"
    network_access = "disabled"
    isolation = "container"
    credential_access = "none"

    def prepare(self, repo: RepoState) -> None:
        return None

    def run(self, repo: RepoState, spec: ExecSpec) -> ExecOutcome:
        return ExecOutcome(0, f"1 passed {SECRET}", "", duration_s=0.2)


def _resign_bug_bundle(bundle: Path, output: Path, mutate) -> Path:
    with zipfile.ZipFile(bundle) as archive:
        blobs = {name: archive.read(name) for name in archive.namelist()}
    case = json.loads(blobs["case.json"])
    mutate(case, blobs)
    blobs["case.json"] = eef._canonical(case) + b"\n"
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


def _resign_passport(payload: dict) -> None:
    unsigned = dict(payload)
    del unsigned["passport_signature"]
    payload["passport_signature"]["value"] = hmac.new(
        KEY,
        b"exhibit-a-passport/v1\0" + eef._canonical(unsigned),
        hashlib.sha256,
    ).hexdigest()


def _bug_bundle(tmp_path: Path) -> Path:
    target = tmp_path / "target"
    base = tmp_path / "base"
    target.mkdir()
    base.mkdir()
    (target / "inventory.py").write_text(f"TOKEN = {SECRET!r}\n")
    (base / "inventory.py").write_text("TOKEN = None\n")
    test_code = (
        f"from inventory import TOKEN\n\ndef test_repro():\n    assert TOKEN != {SECRET!r}\n"
    )
    case = Case(id=f"case-{SECRET}", mode=Mode.DETECTIVE)
    case.claim_text = f"claim contains {SECRET}"
    case.root_cause_narrative = f"narrative contains {SECRET}"
    case.test_file = CaseTestArtifact("test_repro.py", test_code)
    case.run_command = "python3 -m pytest -x -q test_repro.py"
    case.verdict = Verdict.VERIFIED
    case.truth.execution = ExecutionTruth.COMPLETED
    case.truth.goal = GoalTruth.VERIFIED
    case.truth.release = ReleaseTruth.NOT_ASSESSED
    case.evidence = Evidence(
        fail_log=f"E   AssertionError: failed with {SECRET}",
        fail_signature="AssertionError",
        pass_log=f"passed with {SECRET}",
        reruns=2,
        deterministic=True,
        runs=[
            RunResult(
                "target",
                1,
                False,
                f"E   AssertionError: failed with {SECRET}",
                "AssertionError: failed",
            ),
            RunResult(
                "target",
                1,
                False,
                f"E   AssertionError: failed with {SECRET}",
                "AssertionError: failed",
            ),
            RunResult("base", 0, True, f"passed with {SECRET}"),
        ],
    )
    case.proposal_runs = [
        ProposalRun(
            operation="propose",
            provider="anthropic",
            requested_model=PLAIN_SECRET,
            confirmed_model="sk-ant-api03-ConfirmedCredential456",
            confirmed_version="unknown_unverified_backend",
            output_sha256="a" * 64,
            input_tokens=20,
            output_tokens=5,
            total_tokens=25,
        )
    ]
    return create_bundle(
        case.to_dict(),
        tmp_path / "bug.eef",
        target_source=target,
        base_source=base,
        signing_key=KEY,
    )


def _refactor_bundle(tmp_path: Path) -> Path:
    base = tmp_path / "refactor-base"
    target = tmp_path / "refactor-target"
    base.mkdir()
    target.mkdir()
    (base / "behavior.py").write_text(f"VALUE = {SECRET!r}\n")
    (target / "behavior.py").write_text(f"VALUE = {SECRET!r}\n")
    contract = f"# {SECRET}\ndef test_contract():\n    assert True\n"
    evidence = collect_refactor_evidence(
        PassingExecutor(),
        RepoState(
            str(base),
            "base",
            source=f"https://user:secret@example.com/hooks/{PATH_SECRET}?token=x",
        ),
        RepoState(str(target), "target", source=str(target)),
        contract,
        reruns=2,
    )
    return create_refactor_bundle(
        evidence,
        tmp_path / "refactor.eef",
        base_source=base,
        target_source=target,
        signing_key=KEY,
    )


def test_bug_passport_is_verified_hash_linked_and_credential_free(tmp_path: Path):
    bundle = _bug_bundle(tmp_path)

    verified = read_verified_claim(bundle, signing_key=KEY)
    passport = passport_from_verified_claim(verified)
    encoded = json.dumps(passport, sort_keys=True)

    assert passport["schema_version"] == PASSPORT_SCHEMA
    assert passport["claim_type"] == "bug_flip"
    assert passport["subject"]["verdict"] == "VERIFIED"
    assert passport["subject"]["truth"] == {
        "execution": "COMPLETED",
        "goal": "VERIFIED",
        "release": "NOT_ASSESSED",
    }
    proposal = passport["subject"]["proposal_runs"][0]
    assert proposal["provider"].startswith("sha256:")
    assert proposal["requested_model"].startswith("sha256:")
    assert proposal["confirmed_model"].startswith("sha256:")
    assert proposal["confirmed_version"] == "unknown_unverified_backend"
    assert passport["verification"]["manifest_sha256"] == verified.manifest_sha256
    assert passport["verification"]["publisher_signature_verified"] is True
    assert SECRET not in encoded
    assert "user:secret" not in encoded
    assert PLAIN_SECRET not in encoded
    assert "ConfirmedCredential456" not in encoded
    assert "test_repro.py" not in encoded
    assert KEY.decode() not in encoded


def test_refactor_passport_omits_private_contract_logs_sources_and_paths(tmp_path: Path):
    bundle = _refactor_bundle(tmp_path)

    passport = passport_from_verified_claim(read_verified_claim(bundle, signing_key=KEY))
    encoded = json.dumps(passport, sort_keys=True)

    assert passport["claim_type"] == "behavior_preserving_refactor"
    assert passport["subject"]["verdict"] == "VERIFIED"
    assert passport["subject"]["states"] == {
        "base": {"status": "PASS", "runs": 2, "exit_codes": [0, 0]},
        "target": {"status": "PASS", "runs": 2, "exit_codes": [0, 0]},
    }
    sources = passport["subject"]["evidence_sources"]
    assert "local-checkout" in {source["source"] for source in sources}
    assert all(
        source["source"] == "local-checkout" or source["source"].startswith("sha256:")
        for source in sources
    )
    assert SECRET not in encoded
    assert "user:secret" not in encoded
    assert PATH_SECRET not in encoded
    assert str(tmp_path) not in encoded
    assert "test_refactor_contract.py" not in encoded


def test_passport_json_is_deterministic_and_requires_valid_signature(tmp_path: Path):
    bundle = _bug_bundle(tmp_path)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    create_passport(bundle, first, signing_key=KEY)
    create_passport(bundle, second, signing_key=KEY)

    assert first.read_bytes() == second.read_bytes()
    payload = json.loads(first.read_text())
    assert verify_passport(payload, signing_key=KEY)
    payload["subject"]["verdict"] = "FAILED"
    assert not verify_passport(payload, signing_key=KEY)
    with pytest.raises(ValueError, match="signature verification failed"):
        create_passport(bundle, tmp_path / "invalid.json", signing_key=b"x" * 32)
    assert not (tmp_path / "invalid.json").exists()
    with pytest.raises(ValueError, match="at least 32 bytes"):
        create_passport(bundle, tmp_path / "short.json", signing_key=b"short")
    with pytest.raises(ValueError, match="must not overwrite"):
        create_passport(bundle, bundle, signing_key=KEY)


def test_passport_cli_writes_verified_json_and_protects_inputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    bundle = _bug_bundle(tmp_path)
    key = tmp_path / "eef.key"
    key.write_bytes(KEY)
    output = tmp_path / "passport.json"

    assert (
        main(
            [
                "passport",
                str(bundle),
                "--signing-key",
                str(key),
                "--out",
                str(output),
            ]
        )
        == 0
    )
    assert capsys.readouterr().out.strip() == str(output)
    assert json.loads(output.read_text())["schema_version"] == PASSPORT_SCHEMA

    assert (
        main(
            [
                "passport",
                str(bundle),
                "--signing-key",
                str(key),
                "--out",
                str(key),
            ]
        )
        == 2
    )
    assert "must not overwrite" in capsys.readouterr().err
    assert key.read_bytes() == KEY


def test_passport_rejects_signed_bug_truth_that_contradicts_run_records(tmp_path: Path):
    bundle = _bug_bundle(tmp_path)
    contradictory = _resign_bug_bundle(
        bundle,
        tmp_path / "contradictory.eef",
        lambda case, _blobs: case["truth"].__setitem__("goal", "UNCERTAIN"),
    )

    with pytest.raises(ValueError, match="truth does not match"):
        create_passport(contradictory, tmp_path / "passport.json", signing_key=KEY)


def test_passport_rejects_verified_bug_without_an_archived_base_state(tmp_path: Path):
    bundle = _bug_bundle(tmp_path)

    def remove_base(_case, blobs):
        for name in [name for name in blobs if name.startswith("sources/base/")]:
            del blobs[name]

    fabricated = _resign_bug_bundle(bundle, tmp_path / "fabricated-base.eef", remove_base)

    with pytest.raises(ValueError, match="requires an archived base state"):
        create_passport(fabricated, tmp_path / "passport.json", signing_key=KEY)


def test_passport_rejects_an_eef_signature_replayed_across_protocols(tmp_path: Path):
    bundle = _bug_bundle(tmp_path)
    with zipfile.ZipFile(bundle) as archive:
        attestation = json.loads(archive.read("attestation.json"))
    forged = dict(attestation["statement"])
    forged["passport_signature"] = {
        "algorithm": "hmac-sha256",
        "value": attestation["signature"]["value"],
        "meaning": "shared-key authenticity; publisher identity is not established",
    }

    with pytest.raises(ValueError, match="document shape"):
        verify_passport(forged, signing_key=KEY)


def test_passport_atomic_output_protects_hardlinked_inputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    bundle = _bug_bundle(tmp_path)
    key = tmp_path / "eef.key"
    key.write_bytes(KEY)
    key_link = tmp_path / "key-link.json"
    key_link.hardlink_to(key)

    assert main(["passport", str(bundle), "--signing-key", str(key), "--out", str(key_link)]) == 2
    assert "must not overwrite" in capsys.readouterr().err
    assert key.read_bytes() == KEY

    bundle_link = tmp_path / "bundle-link.json"
    bundle_link.hardlink_to(bundle)
    original = bundle.read_bytes()
    with pytest.raises(ValueError, match="must not overwrite"):
        create_passport(bundle, bundle_link, signing_key=KEY)
    assert bundle.read_bytes() == original


def test_passport_enforces_final_encoded_size_before_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    bundle = _bug_bundle(tmp_path)
    output = tmp_path / "oversized.json"
    monkeypatch.setattr(passport_module, "_MAX_PASSPORT_BYTES", 10)

    with pytest.raises(ValueError, match="size limit"):
        create_passport(bundle, output, signing_key=KEY)
    assert not output.exists()


def test_html_passport_is_deterministic_standalone_and_credential_free(tmp_path: Path):
    bundle = _bug_bundle(tmp_path)
    passport = create_passport(bundle, tmp_path / "passport.json", signing_key=KEY)
    first = create_html_passport(passport, tmp_path / "first.html", signing_key=KEY)
    second = create_html_passport(passport, tmp_path / "second.html", signing_key=KEY)

    assert first.read_bytes() == second.read_bytes()
    rendered = first.read_text()
    assert rendered.startswith("<!doctype html>")
    assert "Content-Security-Policy" in rendered
    assert "default-src 'none'" in rendered
    assert "<script" not in rendered
    assert "https://" not in rendered
    assert SECRET not in rendered
    assert PLAIN_SECRET not in rendered
    assert "Three truths, kept separate" in rendered
    assert "VERIFIED" in rendered


def test_html_passport_labels_revisions_by_the_state_they_assert(tmp_path: Path):
    failing = "3c3ec8996383750423f6f32d398850cd7af889e5"
    passing = "1f9473f8d6940935ec45a41cb518d9038e0bea0e"

    def add_revisions(case, _blobs):
        # A Case stores the failing state as base_commit and the passing state as
        # target_commit; the rendered passport must not leak that inversion to a reader.
        case["base_commit"] = failing
        case["target_commit"] = passing

    bundle = _resign_bug_bundle(_bug_bundle(tmp_path), tmp_path / "revised.eef", add_revisions)
    passport = create_passport(bundle, tmp_path / "revised.json", signing_key=KEY)
    rendered = create_html_passport(
        passport, tmp_path / "revised.html", signing_key=KEY
    ).read_text()

    assert f'<dt>Failing revision</dt><dd class="mono">{failing}</dd>' in rendered
    assert f'<dt>Passing revision</dt><dd class="mono">{passing}</dd>' in rendered
    assert "<dt>Target reruns</dt>" not in rendered


def test_refactor_html_passport_renders_both_verified_states(tmp_path: Path):
    bundle = _refactor_bundle(tmp_path)
    passport = create_passport(bundle, tmp_path / "refactor.json", signing_key=KEY)
    output = create_html_passport(passport, tmp_path / "refactor.html", signing_key=KEY)

    rendered = output.read_text()
    assert "Behavior-preserving refactor" in rendered
    assert "Base state" in rendered
    assert "Target state" in rendered
    assert rendered.count(">PASS<") == 2
    assert SECRET not in rendered


def test_html_passport_escapes_signed_text_and_rejects_wrong_key(tmp_path: Path):
    bundle = _bug_bundle(tmp_path)
    passport = create_passport(bundle, tmp_path / "passport.json", signing_key=KEY)
    payload = json.loads(passport.read_text())
    payload["subject"]["verdict"] = "<script>alert(1)</script>"
    _resign_passport(payload)
    passport.write_text(json.dumps(payload))

    rendered = render_html_passport(payload)
    assert "<script>alert(1)</script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    with pytest.raises(ValueError, match="bug passport subject"):
        create_html_passport(passport, tmp_path / "passport.html", signing_key=KEY)
    with pytest.raises(ValueError, match="signature verification failed"):
        create_html_passport(passport, tmp_path / "wrong.html", signing_key=b"x" * 32)


def test_html_passport_rejects_mac_valid_misleading_summaries(tmp_path: Path):
    bug_bundle = _bug_bundle(tmp_path)
    bug = create_passport(bug_bundle, tmp_path / "bug.json", signing_key=KEY)
    bug_payload = json.loads(bug.read_text())
    bug_payload["verification"]["integrity_verified"] = False
    _resign_passport(bug_payload)
    bug.write_text(json.dumps(bug_payload))
    with pytest.raises(ValueError, match="verification claims"):
        create_html_passport(bug, tmp_path / "bug.html", signing_key=KEY)

    refactor_bundle = _refactor_bundle(tmp_path)
    refactor = create_passport(refactor_bundle, tmp_path / "refactor.json", signing_key=KEY)
    refactor_payload = json.loads(refactor.read_text())
    refactor_payload["subject"]["states"]["base"]["runs"] = 99
    _resign_passport(refactor_payload)
    refactor.write_text(json.dumps(refactor_payload))
    with pytest.raises(ValueError, match="state observation"):
        create_html_passport(refactor, tmp_path / "refactor.html", signing_key=KEY)


def test_html_passport_rejects_contradictory_refactor_state_summaries(tmp_path: Path):
    bundle = _refactor_bundle(tmp_path)
    passport = create_passport(bundle, tmp_path / "source.json", signing_key=KEY)
    baseline = json.loads(passport.read_text())

    contradictory = []
    run_mismatch = json.loads(json.dumps(baseline))
    run_mismatch["subject"]["states"]["base"].update({"runs": 3, "exit_codes": [0, 0, 0]})
    contradictory.append((run_mismatch, "runs do not match"))

    bad_exit = json.loads(json.dumps(baseline))
    bad_exit["subject"]["states"]["base"]["exit_codes"] = [5, 5]
    contradictory.append((bad_exit, "status contradicts"))

    false_failure = json.loads(json.dumps(baseline))
    false_failure["subject"]["verdict"] = "FAILED"
    false_failure["subject"]["truth"]["goal"] = "FAILED"
    contradictory.append((false_failure, "FAILED summary"))

    zero_run = json.loads(json.dumps(baseline))
    zero_run["subject"]["states"]["base"].update({"runs": 0, "exit_codes": []})
    contradictory.append((zero_run, "status contradicts"))

    failed_flaky = json.loads(json.dumps(baseline))
    failed_flaky["subject"]["verdict"] = "FAILED"
    failed_flaky["subject"]["truth"]["goal"] = "FAILED"
    failed_flaky["subject"]["states"]["base"].update({"status": "FLAKY", "exit_codes": [0, 1]})
    contradictory.append((failed_flaky, "unstable state"))

    uncertain_pass = json.loads(json.dumps(baseline))
    uncertain_pass["subject"]["verdict"] = "UNCERTAIN"
    uncertain_pass["subject"]["truth"]["goal"] = "UNCERTAIN"
    uncertain_pass["subject"]["deterministic"] = False
    contradictory.append((uncertain_pass, "complete passing states"))

    uncertain_extra_run = json.loads(json.dumps(baseline))
    uncertain_extra_run["subject"]["verdict"] = "UNCERTAIN"
    uncertain_extra_run["subject"]["truth"]["goal"] = "UNCERTAIN"
    uncertain_extra_run["subject"]["deterministic"] = False
    uncertain_extra_run["subject"]["states"]["base"].update(
        {"status": "FLAKY", "runs": 3, "exit_codes": [0, 1, 0]}
    )
    contradictory.append((uncertain_extra_run, "runs do not match"))

    impossible_flaky = json.loads(json.dumps(baseline))
    impossible_flaky["subject"]["verdict"] = "UNCERTAIN"
    impossible_flaky["subject"]["truth"]["goal"] = "UNCERTAIN"
    impossible_flaky["subject"]["deterministic"] = False
    impossible_flaky["subject"]["states"]["base"]["status"] = "FLAKY"
    contradictory.append((impossible_flaky, "status contradicts"))

    for index, (payload, message) in enumerate(contradictory):
        _resign_passport(payload)
        candidate = tmp_path / f"contradictory-{index}.json"
        candidate.write_text(json.dumps(payload))
        with pytest.raises(ValueError, match=message):
            create_html_passport(
                candidate,
                tmp_path / f"contradictory-{index}.html",
                signing_key=KEY,
            )


def test_passport_html_cli_protects_hardlinked_inputs_and_size_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    bundle = _bug_bundle(tmp_path)
    passport = create_passport(bundle, tmp_path / "passport.json", signing_key=KEY)
    key = tmp_path / "eef.key"
    key.write_bytes(KEY)
    output = tmp_path / "passport.html"

    assert (
        main(["passport-html", str(passport), "--signing-key", str(key), "--out", str(output)]) == 0
    )
    assert capsys.readouterr().out.strip() == str(output)

    key_link = tmp_path / "key-link.html"
    key_link.hardlink_to(key)
    assert (
        main(["passport-html", str(passport), "--signing-key", str(key), "--out", str(key_link)])
        == 2
    )
    assert "must not overwrite" in capsys.readouterr().err
    assert key.read_bytes() == KEY

    monkeypatch.setattr(passport_html_module, "_MAX_HTML_BYTES", 10)
    with pytest.raises(ValueError, match="size limit"):
        create_html_passport(passport, tmp_path / "oversized.html", signing_key=KEY)


def test_html_passport_rejects_special_oversized_and_deep_json_inputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    fifo = tmp_path / "passport.fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="regular file"):
        create_html_passport(fifo, tmp_path / "fifo.html", signing_key=KEY)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{" + b" " * (1024 * 1024) + b"}")
    with pytest.raises(ValueError, match="1 MiB"):
        create_html_passport(oversized, tmp_path / "large.html", signing_key=KEY)

    nested = tmp_path / "nested.json"
    nested.write_text("[" * 10_000 + "0" + "]" * 10_000)
    key = tmp_path / "eef.key"
    key.write_bytes(KEY)
    assert (
        main(
            [
                "passport-html",
                str(nested),
                "--signing-key",
                str(key),
                "--out",
                str(tmp_path / "nested.html"),
            ]
        )
        == 2
    )
    assert "nesting limit" in capsys.readouterr().err
