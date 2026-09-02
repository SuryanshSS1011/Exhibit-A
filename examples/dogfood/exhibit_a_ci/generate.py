"""Regenerate Exhibit A's release passport from one frozen public CI receipt."""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import tarfile
import tempfile
from datetime import datetime
from importlib.metadata import version
from pathlib import Path, PurePosixPath

from exhibit_a.eef import create_bundle
from exhibit_a.executor.base import ExecOutcome, ExecSpec, RepoState
from exhibit_a.executor.local_exec import LocalExecutor
from exhibit_a.models.case import (
    Case,
    Evidence,
    ExecutionTruth,
    GoalTruth,
    Mode,
    RunResult,
    TargetKind,
    TestArtifact,
    Verdict,
)
from exhibit_a.passport import create_passport, verify_passport
from exhibit_a.passport_html import create_html_passport
from exhibit_a.release_evidence import (
    connector_output_from_receipt,
    create_release_record,
    parse_policy_document,
)
from exhibit_a.verdict.flip_check import extract_signature, flip_check

BUGGY_SHA = "3c3ec8996383750423f6f32d398850cd7af889e5"
RELEASE_SHA = "de669e7e09aa5694911fe524ab30253f75a6b5cc"
DEMO_KEY = b"exhibit-a-public-release-dogfood-key-v1"
DOGFOOD_PYTEST_VERSION = "9.1.1"
TEST_PATH = "tests/test_timeout_false_verified.py"
TEST_COMMAND = f"python3 -m pytest -x -q -q {TEST_PATH}"
EXPECTED_SIGNATURE = "AssertionError: timed-out target was admitted as evidence"
TEST_CODE = '''from exhibit_a.executor.base import ExecOutcome
from exhibit_a.verdict.flip_check import flip_check


REAL_TEST_CODE = """from exhibit_a.verdict.flip_check import flip_check

def test_real_behavior():
    assert flip_check is not None
"""


def test_timeout_on_target_is_not_evidence_even_without_signature():
    timed_out = ExecOutcome(exit_code=124, stdout="TIMEOUT", stderr="", timed_out=True)
    target = [timed_out for _ in range(3)]
    base = ExecOutcome(exit_code=0, stdout="1 passed", stderr="")
    result = flip_check(
        target_runs=target,
        base_run=base,
        test_code=REAL_TEST_CODE,
        expected_signature=None,
    )
    assert not result.admissible, "timed-out target was admitted as evidence"
'''


def main() -> int:
    installed_pytest = version("pytest")
    if installed_pytest != DOGFOOD_PYTEST_VERSION:
        raise RuntimeError(
            f"dogfood generation requires pytest {DOGFOOD_PYTEST_VERSION}, found {installed_pytest}"
        )
    parser = argparse.ArgumentParser(description=__doc__)
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--out-dir",
        type=Path,
        help="directory for exhibit_a_ci.passport.{json,html}",
    )
    output_group.add_argument(
        "--check",
        action="store_true",
        help="regenerate offline and byte-compare the published artifacts",
    )
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[3]
    published = Path(__file__).resolve().parent
    temporary_parent = Path(tempfile.gettempdir()).resolve()
    check_directory = None
    if args.check:
        check_directory = tempfile.TemporaryDirectory(
            prefix="exhibit-a-ci-dogfood-check-", dir=temporary_parent
        )
        output = Path(check_directory.name)
    else:
        output = (args.out_dir or published).resolve()
    output.mkdir(parents=True, exist_ok=True)

    try:
        json_path, html_path = _generate(repo, published, output, temporary_parent)
        if args.check:
            for generated, name in (
                (json_path, "exhibit_a_ci.passport.json"),
                (html_path, "exhibit_a_ci.passport.html"),
            ):
                if generated.read_bytes() != (published / name).read_bytes():
                    raise RuntimeError(f"published CI dogfood artifact is stale: {name}")
            print("published CI dogfood passports are byte-reproducible offline")
        else:
            print(json_path)
            print(html_path)
        return 0
    finally:
        if check_directory is not None:
            check_directory.cleanup()


def _generate(
    repo: Path,
    fixture_dir: Path,
    output: Path,
    temporary_parent: Path,
) -> tuple[Path, Path]:
    receipt = json.loads((fixture_dir / "ci_status.receipt.json").read_text())
    collected = connector_output_from_receipt(receipt)
    named_policy = parse_policy_document(
        json.loads((fixture_dir / "release-policy.json").read_text())
    )
    evaluated_at = datetime.fromisoformat(collected.provenance.observed_at)

    with tempfile.TemporaryDirectory(
        prefix="exhibit-a-ci-dogfood-", dir=temporary_parent
    ) as temporary:
        root = Path(temporary)
        buggy = root / "buggy"
        fixed = root / "fixed"
        _archive_engine(repo, BUGGY_SHA, buggy)
        _archive_engine(repo, RELEASE_SHA, fixed)

        executor = LocalExecutor()
        spec = ExecSpec(TEST_PATH, TEST_CODE, TEST_COMMAND, timeout_s=30)
        target_outcomes = [
            executor.run(RepoState(str(buggy), "target", BUGGY_SHA), spec) for _ in range(3)
        ]
        base_outcome = executor.run(RepoState(str(fixed), "base", RELEASE_SHA), spec)
        flip = flip_check(
            target_runs=target_outcomes,
            base_run=base_outcome,
            test_code=TEST_CODE,
            expected_signature=EXPECTED_SIGNATURE,
        )
        if not flip.admissible or flip.tier != "flip":
            raise RuntimeError(f"historical dogfood no longer produces a flip: {flip.reason}")

        case = _case(target_outcomes, base_outcome).to_dict()
        record, assessment = create_release_record(
            collected,
            named_policy,
            evaluated_at=evaluated_at,
        )
        case["truth"]["release"] = assessment.release.value
        case["truth"]["release_reason"] = assessment.reason
        case["release_evidence"] = record
        bundle = create_bundle(
            case,
            root / "exhibit_a_ci.eef",
            target_source=buggy,
            base_source=fixed,
            signing_key=DEMO_KEY,
            connector_outputs=(collected,),
        )
        json_path = create_passport(
            bundle,
            output / "exhibit_a_ci.passport.json",
            signing_key=DEMO_KEY,
        )
        html_path = create_html_passport(
            json_path,
            output / "exhibit_a_ci.passport.html",
            signing_key=DEMO_KEY,
        )

    payload = json.loads(json_path.read_text())
    if not verify_passport(payload, signing_key=DEMO_KEY):
        raise RuntimeError("generated public CI dogfood passport did not verify")
    return json_path, html_path


def _case(target_outcomes: list[ExecOutcome], base_outcome: ExecOutcome) -> Case:
    case = Case(
        id="dogfood-exhibit-a-ci-de669e7",
        mode=Mode.DETECTIVE,
        repo="https://github.com/SuryanshSS1011/Exhibit-A",
        base_commit=BUGGY_SHA,
        target_commit=RELEASE_SHA,
        target_state=TargetKind.SYNTHESIZED_PATCH,
        claim_text="Timed-out target executions could be admitted as verified bug evidence.",
        test_file=TestArtifact(TEST_PATH, TEST_CODE),
        run_command=TEST_COMMAND,
        verdict=Verdict.VERIFIED,
        fail_to_pass=[TEST_PATH],
        license_name="MIT",
        created_at="2026-09-02T21:07:18.553485+00:00",
    )
    case.truth.execution = ExecutionTruth.COMPLETED
    case.truth.goal = GoalTruth.VERIFIED
    case.evidence = Evidence(
        fail_log=target_outcomes[0].log,
        fail_signature=extract_signature(target_outcomes[0]),
        pass_log=base_outcome.log,
        reruns=len(target_outcomes),
        deterministic=True,
        runs=[_run_result("target", outcome) for outcome in target_outcomes]
        + [_run_result("base", base_outcome)],
    )
    return case


def _run_result(state: str, outcome: ExecOutcome) -> RunResult:
    return RunResult(
        state=state,
        exit_code=outcome.exit_code,
        passed=outcome.passed,
        log=outcome.log,
        signature=extract_signature(outcome),
    )


def _archive_engine(repo: Path, revision: str, destination: Path) -> None:
    archive = subprocess.run(
        ["git", "archive", "--format=tar", f"{revision}:engine"],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        for member in tar.getmembers():
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"unsafe historical archive path: {member.name}")
            path = destination.joinpath(*relative.parts)
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ValueError(f"unsupported historical archive entry: {member.name}")
            source = tar.extractfile(member)
            if source is None:
                raise ValueError(f"historical archive entry could not be read: {member.name}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(source.read())
            path.chmod(member.mode & 0o777)


if __name__ == "__main__":
    raise SystemExit(main())
