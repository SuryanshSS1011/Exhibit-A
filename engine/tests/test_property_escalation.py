from __future__ import annotations

import json
from pathlib import Path

from exhibit_a.executor.local_exec import LocalExecutor
from exhibit_a.hypothesis.generator import Candidate
from exhibit_a.hypothesis.property import PropertyCandidate
from exhibit_a.studies.property_escalation import (
    PropertyStatus,
    property_kind,
    run_property_escalation,
)


class FixedPropertyGenerator:
    def __init__(self, code: str):
        self.code = code

    def propose(self, case: dict, repo_path: str):
        return PropertyCandidate(
            Candidate(
                "eligibility is inclusive over the boundary domain",
                "test_property_repro.py",
                self.code,
                "python3 -m pytest -x -q test_property_repro.py",
                "AssertionError",
            ),
            "integer values 10 through 12",
        )


def _fixtures(tmp_path: Path) -> tuple[Path, Path, Path]:
    target = tmp_path / "target"
    base = tmp_path / "base"
    target.mkdir()
    base.mkdir()
    (target / "app.py").write_text("def eligible(value):\n    return value > 10\n")
    (base / "app.py").write_text("def eligible(value):\n    return value >= 10\n")
    case = tmp_path / "case.json"
    case.write_text(
        json.dumps(
            {
                "id": "property-case",
                "verdict": "PROVEN",
                "claim_text": "ten is eligible",
                "test_file": {
                    "path": "test_repro.py",
                    "code": "from app import eligible\n\ndef test_boundary(): assert eligible(10)\n",
                },
                "run_command": "python3 -m pytest -x -q test_repro.py",
                "evidence_strength": {"score": 0.8},
            }
        )
    )
    return target, base, case


def test_parametrized_property_must_clear_real_flip_and_mutation_score(tmp_path: Path):
    target, base, case = _fixtures(tmp_path)
    code = (
        "import pytest\nfrom app import eligible\n\n"
        "@pytest.mark.parametrize('value', [10, 11, 12])\n"
        "def test_eligible_domain(value):\n"
        "    assert eligible(value)\n"
    )

    report = run_property_escalation(
        case,
        target,
        base,
        FixedPropertyGenerator(code),
        LocalExecutor(),
        source_paths=["app.py"],
        reruns=2,
    )

    assert report.status is PropertyStatus.VERIFIED
    assert report.property_kind == "pytest-parametrize"
    assert report.target_passes == (False, False)
    assert report.base_passes == (True, True)
    assert report.mutation_kill_rate == 1.0
    assert report.concrete_strength_score == 0.8


def test_single_example_is_not_mislabeled_as_a_property(tmp_path: Path):
    target, base, case = _fixtures(tmp_path)
    code = "from app import eligible\n\ndef test_one():\n    assert eligible(10)\n"

    report = run_property_escalation(
        case, target, base, FixedPropertyGenerator(code), LocalExecutor()
    )

    assert report.status is PropertyStatus.REJECTED
    assert "neither Hypothesis" in (report.reason or "")


def test_hypothesis_given_is_recognized_structurally():
    code = (
        "from hypothesis import given, strategies as st\nfrom app import eligible\n\n"
        "@given(st.integers(min_value=10))\n"
        "def test_property(value):\n    assert eligible(value)\n"
    )

    assert property_kind(code) == "hypothesis"
