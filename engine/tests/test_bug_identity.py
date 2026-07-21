from __future__ import annotations

import json
from pathlib import Path

from exhibit_a.executor.local_exec import LocalExecutor
from exhibit_a.studies.bug_identity import MANIFEST_VERSION, PairStatus, run_bug_identity


def _repo(path: Path, *, x_fixed: bool, y_fixed: bool) -> None:
    path.mkdir()
    x_operator = ">=" if x_fixed else ">"
    y_offset = "0" if y_fixed else "1"
    (path / "app.py").write_text(
        f"def at_least_ten(value):\n    return value {x_operator} 10\n\n"
        f"def double(value):\n    return value * 2 + {y_offset}\n"
    )


def _case(root: Path, case_id: str, assertion: str) -> str:
    path = root / f"{case_id}.json"
    test_path = f"test_{case_id}.py"
    path.write_text(
        json.dumps(
            {
                "id": case_id,
                "verdict": "PROVEN",
                "test_file": {
                    "path": test_path,
                    "code": f"from app import at_least_ten, double\n\ndef test_repro():\n    {assertion}\n",
                },
                "run_command": f"python3 -m pytest -x -q {test_path}",
                "evidence": {"fail_signature": "AssertionError"},
            }
        )
    )
    return path.name


def test_execution_dedup_finds_equivalent_and_distinct_bugs(tmp_path: Path):
    _repo(tmp_path / "target-a", x_fixed=False, y_fixed=False)
    _repo(tmp_path / "fixed-a", x_fixed=True, y_fixed=False)
    _repo(tmp_path / "target-b", x_fixed=False, y_fixed=False)
    _repo(tmp_path / "fixed-b", x_fixed=True, y_fixed=False)
    _repo(tmp_path / "target-c", x_fixed=False, y_fixed=False)
    _repo(tmp_path / "fixed-c", x_fixed=False, y_fixed=True)
    cases = [
        {
            "case": _case(tmp_path, "case_a", "assert at_least_ten(10)"),
            "target": "target-a",
            "fixed": "fixed-a",
        },
        {
            "case": _case(tmp_path, "case_b", "assert at_least_ten(10) is True"),
            "target": "target-b",
            "fixed": "fixed-b",
        },
        {
            "case": _case(tmp_path, "case_c", "assert double(3) == 6"),
            "target": "target-c",
            "fixed": "fixed-c",
        },
    ]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": MANIFEST_VERSION, "cases": cases}))

    report = run_bug_identity(manifest, LocalExecutor(), reruns=2)

    assert report.invalid_cases == {}
    statuses = {(pair.left, pair.right): pair.status for pair in report.pairs}
    assert statuses[("case_a", "case_b")] is PairStatus.EQUIVALENT
    assert statuses[("case_a", "case_c")] is PairStatus.DISTINCT
    assert statuses[("case_b", "case_c")] is PairStatus.DISTINCT
    assert report.clusters == (("case_a", "case_b"), ("case_c",))


def test_dedup_excludes_case_when_sealed_flip_no_longer_revalidates(tmp_path: Path):
    _repo(tmp_path / "target", x_fixed=True, y_fixed=False)
    _repo(tmp_path / "fixed", x_fixed=True, y_fixed=False)
    case = {
        "case": _case(tmp_path, "stale", "assert at_least_ten(10)"),
        "target": "target",
        "fixed": "fixed",
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": MANIFEST_VERSION, "cases": [case]}))

    report = run_bug_identity(manifest, LocalExecutor(), reruns=1)

    assert report.valid_cases == ()
    assert "no longer fails" in report.invalid_cases["stale"]
    assert report.clusters == ()
