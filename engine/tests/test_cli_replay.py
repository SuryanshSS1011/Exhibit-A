from __future__ import annotations

import json
from pathlib import Path

from exhibit_a.cli import main


def _case(verdict: str = "VERIFIED") -> dict:
    return {
        "id": "demo-case",
        "verdict": verdict,
        "evidence": {"runs": []},
        "hypotheses": [],
    }


def test_replay_emits_terminal_case_without_repo(tmp_path: Path, capsys):
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(_case()))

    result = main(["repro", "--replay", str(case_path), "--events"])

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert result == 0
    assert lines[0]["phase"] == "replay"
    assert lines[-1] == {"event": "case", "case": _case()}


def test_replay_preserves_silence_exit_code(tmp_path: Path):
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(_case("UNCERTAIN")))

    assert main(["repro", "--replay", str(case_path)]) == 1


def test_replay_upgrades_legacy_verdict_values(tmp_path: Path, capsys):
    case_path = tmp_path / "case.json"
    case = _case("PROVEN")
    case["disposition"] = "REPRODUCED"
    case_path.write_text(json.dumps(case))

    assert main(["repro", "--replay", str(case_path), "--json"]) == 0
    output = capsys.readouterr().out
    assert '"verdict": "VERIFIED"' in output
    assert '"disposition": "PARTIAL"' in output


def test_replay_rejects_invalid_case(tmp_path: Path, capsys):
    case_path = tmp_path / "case.json"
    case_path.write_text('{"verdict": "VERIFIED"}')

    assert main(["repro", "--replay", str(case_path)]) == 2
    assert "missing a valid id" in capsys.readouterr().err
