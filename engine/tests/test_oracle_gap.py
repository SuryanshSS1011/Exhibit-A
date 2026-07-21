from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from exhibit_a.cli import main
from exhibit_a.executor.local_exec import LocalExecutor
from exhibit_a.studies.oracle_gap import (
    MANIFEST_VERSION,
    load_oracle_manifest,
    run_oracle_gap,
    save_oracle_gap_report,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _manifest(tmp_path: Path, *, test_code: str) -> Path:
    resolved = tmp_path / "resolved"
    shutil.copytree(FIXTURES / "fixed_slice", resolved)
    (resolved / "official_test.py").write_text(test_code)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": MANIFEST_VERSION,
                "instances": [
                    {
                        "instance_id": "owner__repo-1",
                        "resolved": "resolved",
                        "test_path": "official_test.py",
                        "source_paths": ["slicer.py"],
                        "suspect_lines": {"slicer.py": [9]},
                    }
                ],
            }
        )
    )
    return manifest


def test_oracle_gap_records_surviving_official_test_mutants(tmp_path: Path):
    manifest = _manifest(
        tmp_path,
        test_code=(
            "from slicer import last_n\n\n"
            "def test_official_contract():\n"
            "    assert last_n([1, 2, 3, 4], 2) == [3, 4]\n"
        ),
    )
    original = (tmp_path / "resolved" / "slicer.py").read_text()

    report = run_oracle_gap(manifest, LocalExecutor(), reruns=2)

    assert report.instances == report.evaluated_instances == 1
    assert report.generated == report.eligible == report.survived == 1
    assert report.killed == report.invalid == 0
    assert report.kill_rate == 0.0
    assert report.oracle_gap == 1.0
    assert report.items[0].survivors == ("slicer.py:9:17:-->+",)
    assert (tmp_path / "resolved" / "slicer.py").read_text() == original


def test_oracle_gap_records_killed_mutants_and_saves_versioned_report(tmp_path: Path):
    manifest = _manifest(
        tmp_path,
        test_code=(
            "from slicer import last_n\n\n"
            "def test_official_contract():\n"
            "    assert last_n([1, 2, 3, 4], 1) == [4]\n"
        ),
    )

    report = run_oracle_gap(manifest, LocalExecutor(), reruns=2)
    path = save_oracle_gap_report(report, tmp_path / "reports")

    assert report.killed == report.eligible == 1
    assert report.oracle_gap == 0.0
    assert json.loads(path.read_text())["schema_version"] == "oracle-gap/v1"


def test_oracle_manifest_rejects_paths_outside_corpus(tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": MANIFEST_VERSION,
                "instances": [
                    {
                        "instance_id": "unsafe",
                        "resolved": "../fixtures/fixed_slice",
                        "test_path": "test.py",
                        "source_paths": ["slicer.py"],
                    }
                ],
            }
        )
    )

    with pytest.raises(ValueError, match="unsafe manifest path"):
        load_oracle_manifest(manifest)


def test_oracle_gap_cli_writes_private_report(tmp_path: Path):
    manifest = _manifest(
        tmp_path,
        test_code=(
            "from slicer import last_n\n\n"
            "def test_official_contract():\n"
            "    assert last_n([1, 2, 3, 4], 1) == [4]\n"
        ),
    )
    output = tmp_path / "reports"

    assert main(["oracle-gap", str(manifest), "--out", str(output), "--reruns", "1"]) == 0
    reports = list(output.glob("*.json"))
    assert len(reports) == 1
