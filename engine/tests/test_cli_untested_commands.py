"""The two commands the survey found with no test reference anywhere.

Both read a Case from disk and act on it, so both can be handed something that is not one.
These cover the refusal paths: an operator who mistypes a path or points at the wrong JSON
should get a stated reason and a non-zero exit, not a traceback or a silent success.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from exhibit_a import cli


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_bundle_refuses_a_missing_case(capsys: pytest.CaptureFixture[str], tmp_path: Path):
    code, output = _run(
        capsys,
        "bundle",
        str(tmp_path / "absent.json"),
        "--target-source",
        "https://github.com/owner/repo.git",
        "--signing-key",
        str(tmp_path / "key"),
        "--out",
        str(tmp_path / "out.eef"),
    )

    assert code != 0
    assert "cannot create EEF bundle" in output
    assert not (tmp_path / "out.zip").exists()


def test_bundle_refuses_a_case_that_is_not_json(capsys: pytest.CaptureFixture[str], tmp_path: Path):
    case = tmp_path / "case.json"
    case.write_text("not json at all")
    key = tmp_path / "key"
    key.write_bytes(b"0" * 32)

    code, output = _run(
        capsys,
        "bundle",
        str(case),
        "--target-source",
        "https://github.com/owner/repo.git",
        "--signing-key",
        str(key),
        "--out",
        str(tmp_path / "out.eef"),
    )

    assert code != 0
    assert "cannot create EEF bundle" in output


def test_bundle_refuses_a_missing_signing_key(capsys: pytest.CaptureFixture[str], tmp_path: Path):
    # An unsigned bundle is not a weaker bundle, it is not evidence at all, so this must
    # fail rather than fall back to writing one.
    case = tmp_path / "case.json"
    case.write_text(json.dumps({"id": "c1", "verdict": "VERIFIED"}))

    code, output = _run(
        capsys,
        "bundle",
        str(case),
        "--target-source",
        "https://github.com/owner/repo.git",
        "--signing-key",
        str(tmp_path / "absent-key"),
        "--out",
        str(tmp_path / "out.eef"),
    )

    assert code != 0
    assert not (tmp_path / "out.zip").exists()


def test_observe_refuses_a_case_with_no_generated_test(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    # Observation replays the Case's own test. A Case that never produced one has nothing
    # to observe, and saying so beats replaying an empty command.
    case = tmp_path / "case.json"
    case.write_text(json.dumps({"id": "c1", "verdict": "UNCERTAIN", "test_file": None}))

    code, output = _run(
        capsys,
        "observe",
        str(case),
        "https://github.com/owner/repo.git",
        "--upstream-sha",
        "a" * 40,
        "--out",
        str(tmp_path / "research"),
    )

    assert code != 0
    assert "no generated test" in output or "observatory" in output


def test_observe_refuses_a_missing_case(capsys: pytest.CaptureFixture[str], tmp_path: Path):
    code, output = _run(
        capsys,
        "observe",
        str(tmp_path / "absent.json"),
        "https://github.com/owner/repo.git",
        "--upstream-sha",
        "a" * 40,
        "--out",
        str(tmp_path / "research"),
    )

    assert code != 0
