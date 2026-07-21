from __future__ import annotations

from pathlib import Path

import pytest

from exhibit_a.models.case import EvidenceMinimization
from exhibit_a.verdict.evidence_strength import (
    SCHEMA_VERSION,
    compute_evidence_strength,
    imported_source_paths,
    traceback_source_paths,
)
from exhibit_a.verdict.mutation_testing import MutationScore


def _mutation_score(killed: int, survived: int) -> MutationScore:
    eligible = killed + survived
    return MutationScore(
        baseline_passed=True,
        baseline_runs=(),
        generated=eligible,
        eligible=eligible,
        killed=killed,
        survived=survived,
        invalid=0,
        kill_rate=killed / eligible,
    )


def test_full_strength_requires_all_five_measured_components():
    strength = compute_evidence_strength(
        fail_signature="KeyError: 'missing'",
        deterministic=True,
        reruns=5,
        minimization=EvidenceMinimization(
            verified=True,
            original_lines=18,
            minimized_lines=4,
        ),
        mutation_score=_mutation_score(4, 0),
        source_frames=("inventory.py",),
        root_cause_paths=("inventory.py",),
    )

    assert strength.schema_version == SCHEMA_VERSION
    assert strength.composite == 1.0
    assert strength.coverage == 1.0
    assert strength.mutation.score == 1.0
    assert strength.signature.score == 1.0
    assert strength.determinism.score == 1.0
    assert strength.minimality.score == 1.0
    assert strength.surface_distance.score == 1.0


def test_missing_measurements_reduce_coverage_without_becoming_zero_scores():
    strength = compute_evidence_strength(
        fail_signature="AssertionError",
        deterministic=True,
        reruns=1,
        minimization=None,
        mutation_score=None,
    )

    assert strength.coverage == pytest.approx(0.4)
    assert strength.composite == pytest.approx(0.3)
    assert strength.mutation.score is None
    assert strength.minimality.score is None
    assert strength.surface_distance.score is None
    assert strength.signature.score == 0.4
    assert strength.determinism.score == 0.2


@pytest.mark.parametrize(
    ("signature", "expected"),
    [
        ("KeyError: 'missing'", 1.0),
        ("KeyError", 0.8),
        ("AssertionError: assert 1 == 2", 0.65),
        ("AssertionError", 0.4),
        (None, None),
    ],
)
def test_signature_specificity_rewards_typed_value_details(
    signature: str | None, expected: float | None
):
    strength = compute_evidence_strength(
        fail_signature=signature,
        deterministic=True,
        reruns=5,
        minimization=None,
        mutation_score=None,
    )

    assert strength.signature.score == expected


def test_traceback_distance_counts_repository_layers_to_selected_root(tmp_path: Path):
    (tmp_path / "test_repro.py").write_text("def test_repro(): ...\n")
    package = tmp_path / "app"
    package.mkdir()
    (package / "controller.py").write_text("def call(): ...\n")
    (package / "service.py").write_text("def lookup(): ...\n")
    log = (
        "test_repro.py:1: in test_repro\n"
        "app/controller.py:1: in call\n"
        "app/service.py:1: in lookup\n"
        "E   KeyError: missing\n"
    )

    frames = traceback_source_paths(log, str(tmp_path), "test_repro.py")
    strength = compute_evidence_strength(
        fail_signature="KeyError: missing",
        deterministic=True,
        reruns=5,
        minimization=None,
        mutation_score=None,
        source_frames=frames,
        root_cause_paths=("app/service.py",),
    )

    assert frames == ("app/controller.py", "app/service.py")
    assert strength.surface_distance.score == 0.5
    assert "1 repository frame" in strength.surface_distance.basis


def test_local_imports_can_establish_a_mutation_surface_without_a_source_trace(
    tmp_path: Path,
):
    (tmp_path / "inventory.py").write_text("def stock_for(): return 0\n")
    package = tmp_path / "warehouse"
    package.mkdir()
    (package / "__init__.py").write_text("")

    paths = imported_source_paths(
        "from inventory import stock_for\nimport warehouse\nimport pytest\n",
        str(tmp_path),
    )

    assert paths == ("inventory.py", "warehouse/__init__.py")
