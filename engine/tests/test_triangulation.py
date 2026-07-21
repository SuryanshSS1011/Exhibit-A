from __future__ import annotations

import json
from pathlib import Path

import pytest

from exhibit_a.executor.local_exec import LocalExecutor
from exhibit_a.hypothesis.counterpatch import CounterpatchCandidate
from exhibit_a.studies.triangulation import (
    TriangulationStatus,
    run_triangulation,
    validate_counterpatch,
)


class FixedGenerator:
    def __init__(self, patch: str):
        self.patch = patch

    def propose(self, case: dict, repo_path: str, allowed_sources: tuple[str, ...]):
        return CounterpatchCandidate(self.patch, "Use an inclusive boundary.")


def _fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "app.py").write_text("def eligible(value):\n    return value > 10\n")
    (repo / "tests" / "test_existing.py").write_text(
        "from app import eligible\n\ndef test_large_value():\n    assert eligible(11)\n"
    )
    case = tmp_path / "case.json"
    case.write_text(
        json.dumps(
            {
                "id": "boundary-case",
                "verdict": "PROVEN",
                "claim_text": "ten should be eligible",
                "test_file": {
                    "path": "test_repro.py",
                    "code": "from app import eligible\n\ndef test_boundary():\n    assert eligible(10)\n",
                },
                "run_command": "python3 -m pytest -x -q test_repro.py",
                "evidence": {"fail_signature": "AssertionError"},
            }
        )
    )
    patch = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def eligible(value):\n"
        "-    return value > 10\n"
        "+    return value >= 10\n"
    )
    return repo, case, patch


def test_counterpatch_is_viable_only_after_test_and_full_suite_pass(tmp_path: Path):
    repo, case, patch = _fixture(tmp_path)
    original = (repo / "app.py").read_text()

    report = run_triangulation(
        case,
        repo,
        ["app.py"],
        FixedGenerator(patch),
        LocalExecutor(),
        reruns=2,
    )

    assert report.status is TriangulationStatus.VIABLE
    assert report.target_test_passes == (False, False)
    assert report.patched_test_passes == (True, True)
    assert report.baseline_suite_passed is True
    assert report.patched_suite_passed is True
    assert report.touched_files == ("app.py",)
    assert (repo / "app.py").read_text() == original


def test_counterpatch_cannot_touch_tests_or_unapproved_source(tmp_path: Path):
    _repo, _case, patch = _fixture(tmp_path)
    unsafe = patch.replace("app.py", "test_repro.py")

    with pytest.raises(ValueError, match="outside the allowed production scope"):
        validate_counterpatch(unsafe, ("app.py",), "test_repro.py")


def test_counterpatch_rejects_file_creation_and_large_edits(tmp_path: Path):
    _repo, _case, patch = _fixture(tmp_path)

    with pytest.raises(ValueError, match="add or delete"):
        validate_counterpatch(
            patch.replace("--- a/app.py", "--- /dev/null"), ("app.py",), "test_repro.py"
        )
