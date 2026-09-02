from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from exhibit_a.cli import main
from exhibit_a.connectors import (
    CICheckRun,
    CIStatus,
    CIStatusConnector,
    CIStatusRequest,
    ConnectorOutput,
    ConnectorSecurity,
    EvidenceKind,
    EvidenceProvenance,
    Freshness,
    hash_payload,
)
from exhibit_a.eef import read_verified_claim, verify_bundle
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
from exhibit_a.passport import verify_passport

KEY = b"release-evidence-cli-test-key-at-least-32-bytes"
REVISION = "1f9473f8d6940935ec45a41cb518d9038e0bea0e"
OBSERVED = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
EVALUATED = OBSERVED + timedelta(minutes=5)
TOKEN_ENV = "EXHIBIT_A_CI_TOKEN"
TOKEN_SECRET = "ghp_ReleaseEvidenceCredentialCanary"
PRIVATE_CANARY = "RAW_HTTP_BODY_AND_LOCAL_PATH_CANARY"
OPTIONAL_CHECK_CANARY = "private-optional-check-canary"
TEST_CODE = (
    "from inventory import stock_for\n\n"
    "def test_unknown_sku():\n"
    "    assert stock_for([], 'missing') == 0\n"
)


def _case(tmp_path: Path) -> dict:
    case = Case(
        id="release-evidence-cli",
        mode=Mode.DETECTIVE,
        repo="https://github.com/example/project.git",
        base_commit="a" * 40,
        target_commit=REVISION,
        target_state=TargetKind.SYNTHESIZED_PATCH,
    )
    case.created_at = "2026-09-01T11:30:00+00:00"
    case.claim_text = f"private claim {PRIVATE_CANARY} {tmp_path}"
    case.root_cause_narrative = f"private narrative {PRIVATE_CANARY}"
    case.verdict = Verdict.VERIFIED
    case.test_file = CaseTestArtifact("test_repro.py", TEST_CODE)
    case.run_command = "python3 -m pytest -x -q test_repro.py"
    failure = f"E   AssertionError: wrong value {PRIVATE_CANARY} {tmp_path}"
    case.evidence = Evidence(
        fail_log=failure,
        fail_signature="AssertionError: wrong value",
        pass_log=f"1 passed {PRIVATE_CANARY}",
        reruns=2,
        deterministic=True,
        runs=[
            RunResult("target", 1, False, failure, "AssertionError: wrong value"),
            RunResult("target", 1, False, failure, "AssertionError: wrong value"),
            RunResult("base", 0, True, f"1 passed {PRIVATE_CANARY}"),
        ],
    )
    case.truth.execution = ExecutionTruth.COMPLETED
    case.truth.execution_reason = "target and base executions completed"
    case.truth.goal = GoalTruth.VERIFIED
    case.truth.goal_reason = "the generated test produced a deterministic flip"
    return case.to_dict()


def _output(*, conclusion: str = "success") -> ConnectorOutput[CIStatus]:
    checks = (
        CICheckRun(
            "engine",
            "completed",
            conclusion,
            (OBSERVED - timedelta(minutes=3)).isoformat(),
            (OBSERVED - timedelta(minutes=1)).isoformat(),
        ),
        CICheckRun(
            OPTIONAL_CHECK_CANARY,
            "completed",
            "success",
            (OBSERVED - timedelta(minutes=4)).isoformat(),
            (OBSERVED - timedelta(minutes=2)).isoformat(),
        ),
        CICheckRun(
            "web",
            "completed",
            "success",
            (OBSERVED - timedelta(minutes=4)).isoformat(),
            (OBSERVED - timedelta(minutes=2)).isoformat(),
        ),
    )
    status = CIStatus("example/project", REVISION, len(checks), checks)
    source = "https://api.github.com/repos/example/project"
    request_sha256 = hash_payload(
        {"repository": status.repository, "revision": status.revision, "source": source}
    )
    response_sha256 = hash_payload(status.payload())
    provenance = EvidenceProvenance(
        evidence_id="1" * 32,
        connector_id="github_ci_status",
        connector_version="1",
        capability=EvidenceKind.CI_STATUS,
        source=source,
        source_revision=REVISION,
        observed_at=OBSERVED.isoformat(),
        source_updated_at=(OBSERVED - timedelta(minutes=1)).isoformat(),
        freshness=Freshness.POINT_IN_TIME,
        description=f"private collection detail {PRIVATE_CANARY}",
        request_sha256=request_sha256,
        response_sha256=response_sha256,
        artifact_sha256="2" * 64,
        content_sha256=hash_payload(
            {"request_sha256": request_sha256, "response_sha256": response_sha256}
        ),
        security=ConnectorSecurity(
            source_access="read_only",
            network_access="host_unrestricted",
            isolation="in_process",
            credential_access="ambient_host",
        ),
    )
    return ConnectorOutput(status, provenance)


def _fixture(tmp_path: Path) -> dict[str, Path]:
    base = tmp_path / "base"
    target = tmp_path / "target"
    base.mkdir()
    target.mkdir()
    (base / "inventory.py").write_text("def stock_for(rows, sku): return 0\n")
    (target / "inventory.py").write_text("def stock_for(rows, sku): return 1\n")
    case = tmp_path / "case.json"
    policy = tmp_path / "policy.json"
    key = tmp_path / "signing.key"
    case.write_text(json.dumps(_case(tmp_path)))
    policy.write_text(
        json.dumps(
            {
                "schema_version": "release-policy/v1",
                "name": "required-ci",
                "required_checks": ["engine", "web"],
                "max_age_s": 3600,
            }
        )
    )
    key.write_bytes(KEY)
    return {
        "base": base,
        "target": target,
        "case": case,
        "policy": policy,
        "key": key,
        "eef": tmp_path / "release.eef",
        "json": tmp_path / "release.passport.json",
        "html": tmp_path / "release.passport.html",
    }


def _argv(paths: dict[str, Path]) -> list[str]:
    return [
        "release-evidence",
        str(paths["case"]),
        "--target-source",
        str(paths["target"]),
        "--base-source",
        str(paths["base"]),
        "--repository",
        "example/project",
        "--revision",
        REVISION,
        "--policy",
        str(paths["policy"]),
        "--evaluated-at",
        EVALUATED.isoformat(),
        "--token-env",
        TOKEN_ENV,
        "--signing-key",
        str(paths["key"]),
        "--eef-out",
        str(paths["eef"]),
        "--passport-json-out",
        str(paths["json"]),
        "--passport-html-out",
        str(paths["html"]),
    ]


def _replace_option(argv: list[str], option: str, value: str) -> None:
    argv[argv.index(option) + 1] = value


def _mock_collection(
    monkeypatch: pytest.MonkeyPatch,
    output: ConnectorOutput[CIStatus],
) -> list[CIStatusRequest]:
    requests: list[CIStatusRequest] = []

    def collect(_connector: CIStatusConnector, request: CIStatusRequest):
        requests.append(request)
        return output

    monkeypatch.setattr(CIStatusConnector, "collect", collect)
    return requests


def test_release_evidence_cli_mints_verified_safe_private_and_public_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    paths = _fixture(tmp_path)
    requests = _mock_collection(monkeypatch, _output())
    monkeypatch.setenv(TOKEN_ENV, TOKEN_SECRET)

    assert main(_argv(paths)) == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "release": "SAFE",
        "eef": str(paths["eef"]),
        "passport_json": str(paths["json"]),
        "passport_html": str(paths["html"]),
    }
    assert requests == [CIStatusRequest("example/project", REVISION)]
    assert verify_bundle(paths["eef"], signing_key=KEY).signature_verified
    verified = read_verified_claim(paths["eef"], signing_key=KEY)
    assert verified.format_version == "eef/v3"
    assert verified.claim["truth"]["release"] == "SAFE"
    assert verified.claim["release_evidence"]["schema_version"] == "release-evidence/v1"

    passport = json.loads(paths["json"].read_text())
    assert passport["schema_version"] == "exhibit-a-passport/v2"
    assert verify_passport(passport, signing_key=KEY)
    assert passport["subject"]["truth"]["release"] == "SAFE"
    assert [check["name"] for check in passport["release_evidence"]["checks"]] == ["engine", "web"]
    html = paths["html"].read_text()
    assert html.lstrip().lower().startswith("<!doctype html")
    assert "SAFE" in html
    assert "engine" in html
    assert "web" in html
    for canary in (TOKEN_SECRET, PRIVATE_CANARY, OPTIONAL_CHECK_CANARY, str(tmp_path)):
        assert canary not in json.dumps(passport, sort_keys=True)
        assert canary not in html


def test_release_evidence_cli_returns_one_for_unsafe_and_keeps_verified_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    paths = _fixture(tmp_path)
    requests = _mock_collection(monkeypatch, _output(conclusion="failure"))
    monkeypatch.setenv(TOKEN_ENV, TOKEN_SECRET)

    assert main(_argv(paths)) == 1

    assert json.loads(capsys.readouterr().out)["release"] == "UNSAFE"
    assert requests == [CIStatusRequest("example/project", REVISION)]
    assert all(paths[name].is_file() for name in ("eef", "json", "html"))
    verified = read_verified_claim(paths["eef"], signing_key=KEY)
    assert verified.claim["truth"]["release"] == "UNSAFE"
    passport = json.loads(paths["json"].read_text())
    assert verify_passport(passport, signing_key=KEY)
    assert passport["subject"]["truth"]["release"] == "UNSAFE"
    assert "UNSAFE" in paths["html"].read_text()


@pytest.mark.parametrize(
    "refusal",
    [
        "repository_mismatch",
        "revision_branch",
        "revision_mismatch",
        "invalid_token_env",
        "missing_token",
        "unsafe_api_base",
        "invalid_evaluated_at",
        "malformed_policy",
        "existing_output",
        "colliding_outputs",
        "output_in_source",
        "output_in_symlinked_source",
        "malformed_case_command",
        "unsafe_source_symlink",
    ],
)
def test_release_evidence_cli_refuses_invalid_inputs_before_network_and_atomically(
    refusal: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    paths = _fixture(tmp_path)
    argv = _argv(paths)
    requests = _mock_collection(monkeypatch, _output())
    monkeypatch.setenv(TOKEN_ENV, TOKEN_SECRET)
    preexisting: dict[Path, bytes] = {}

    if refusal == "repository_mismatch":
        _replace_option(argv, "--repository", "other/project")
    elif refusal == "revision_branch":
        _replace_option(argv, "--revision", "main")
    elif refusal == "revision_mismatch":
        _replace_option(argv, "--revision", "f" * 40)
    elif refusal == "invalid_token_env":
        _replace_option(argv, "--token-env", "literal-token-value")
    elif refusal == "missing_token":
        monkeypatch.delenv(TOKEN_ENV)
    elif refusal == "unsafe_api_base":
        argv.extend(["--api-base", "https://user:secret@api.github.com"])
    elif refusal == "invalid_evaluated_at":
        _replace_option(argv, "--evaluated-at", "tomorrow")
    elif refusal == "malformed_policy":
        paths["policy"].write_text(json.dumps({"schema_version": "release-policy/v1"}))
    elif refusal == "existing_output":
        paths["eef"].write_bytes(b"existing-private-artifact")
        preexisting[paths["eef"]] = b"existing-private-artifact"
    elif refusal == "colliding_outputs":
        _replace_option(argv, "--passport-json-out", str(paths["eef"]))
    elif refusal == "output_in_source":
        _replace_option(argv, "--eef-out", str(paths["target"] / "nested.eef"))
    elif refusal == "output_in_symlinked_source":
        source_alias = tmp_path / "source-alias"
        source_alias.symlink_to(paths["target"], target_is_directory=True)
        _replace_option(argv, "--eef-out", str(source_alias / "nested.eef"))
    elif refusal == "malformed_case_command":
        case = json.loads(paths["case"].read_text())
        case["run_command"] = "python3 -m pytest test_repro.py; curl example.invalid"
        paths["case"].write_text(json.dumps(case))
    elif refusal == "unsafe_source_symlink":
        (paths["target"] / "unsafe-link").symlink_to(paths["case"])
    else:
        raise AssertionError(f"unknown refusal fixture: {refusal}")

    assert main(argv) == 2

    assert requests == []
    error = capsys.readouterr().err
    assert error.startswith("error: cannot create release evidence:")
    assert {
        "repository_mismatch": "--repository does not match",
        "revision_branch": "full lowercase SHA-1",
        "revision_mismatch": "--revision does not match",
        "invalid_token_env": "environment name is invalid",
        "missing_token": "credential environment",
        "unsafe_api_base": "contains credentials",
        "invalid_evaluated_at": "RFC 3339 timestamp",
        "malformed_policy": "policy document has an invalid shape",
        "existing_output": "must not already exist",
        "colliding_outputs": "three distinct paths",
        "output_in_source": "cannot be inside a source tree",
        "output_in_symlinked_source": "cannot be inside a source tree",
        "malformed_case_command": "shell control character",
        "unsafe_source_symlink": "cannot contain symlinks",
    }[refusal] in error
    output_values = {
        Path(argv[argv.index(option) + 1])
        for option in ("--eef-out", "--passport-json-out", "--passport-html-out")
    }
    for output in output_values:
        if output in preexisting:
            assert output.read_bytes() == preexisting[output]
        else:
            assert not output.exists()


def test_release_evidence_cli_rolls_back_a_partial_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    paths = _fixture(tmp_path)
    _mock_collection(monkeypatch, _output())
    monkeypatch.setenv(TOKEN_ENV, TOKEN_SECRET)
    real_link = os.link
    calls = 0

    def fail_second_link(source, destination, *, follow_symlinks=True):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected publication failure")
        return real_link(source, destination, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os, "link", fail_second_link)

    assert main(_argv(paths)) == 2
    assert calls == 2
    assert not any(paths[name].exists() for name in ("eef", "json", "html"))


def test_release_evidence_cli_never_overwrites_a_destination_created_during_staging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    paths = _fixture(tmp_path)
    _mock_collection(monkeypatch, _output())
    monkeypatch.setenv(TOKEN_ENV, TOKEN_SECRET)
    real_link = os.link
    raced = b"externally-created-destination"

    def race_first_link(source, destination, *, follow_symlinks=True):
        Path(destination).write_bytes(raced)
        return real_link(source, destination, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os, "link", race_first_link)

    assert main(_argv(paths)) == 2
    assert paths["eef"].read_bytes() == raced
    assert not paths["json"].exists()
    assert not paths["html"].exists()
