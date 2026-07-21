from __future__ import annotations

import json
from pathlib import Path

from exhibit_a.cli import main

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def test_offline_study_writes_consistent_silence_as_a_valid_negative_result(tmp_path: Path, capsys):
    out = tmp_path / "research"

    result = main(
        [
            "study",
            str(FIXTURES / "buggy_slice"),
            "--fixed",
            str(FIXTURES / "fixed_slice"),
            "--claim",
            "last_n drops the final element",
            "--expect",
            "AssertionError",
            "--runs",
            "2",
            "--offline",
            "--out",
            str(out),
        ]
    )

    assert result == 0
    assert "strict convergence: no" in capsys.readouterr().out
    paths = list(out.glob("*.json"))
    assert len(paths) == 1
    payload = json.loads(paths[0].read_text())
    assert payload["requested_runs"] == payload["completed_runs"] == 2
    assert payload["variants"] == ["offline-stub"]
    assert payload["verdict"]["convergence"] == 1.0
    assert payload["root_cause"]["coverage"] == 0.0
    assert payload["test_semantics"]["coverage"] == 0.0
    assert payload["converged"] is False
    assert {run["verdict"] for run in payload["runs"]} == {"INSUFFICIENT_EVIDENCE"}


def test_study_rejects_too_few_samples(tmp_path: Path, capsys):
    result = main(
        [
            "study",
            str(FIXTURES / "buggy_slice"),
            "--fixed",
            str(FIXTURES / "fixed_slice"),
            "--claim",
            "claim",
            "--runs",
            "1",
            "--offline",
            "--out",
            str(tmp_path),
        ]
    )

    assert result == 2
    assert "between 2 and 50" in capsys.readouterr().err
