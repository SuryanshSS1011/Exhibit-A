from __future__ import annotations

import json
from pathlib import Path

import pytest

from exhibit_a.hypothesis.generator import Claim
from exhibit_a.models.case import Case, Evidence, Mode, TestArtifact as CaseTestArtifact, Verdict
from exhibit_a.studies.reproducibility import (
    SCHEMA_VERSION,
    run_reproducibility_study,
    save_reproducibility_report,
    semantic_test_fingerprint,
)
from exhibit_a.executor.base import RepoState


TEST_A = """from inventory import stock_for

def test_unknown_sku():
    items = [{"sku": "known", "quantity": 4}]
    assert stock_for(items, "missing") == 0
"""

TEST_A_RENAMED = """from inventory import stock_for
def test_documented_default():
    records = [{"sku": "known", "quantity": 4}]
    assert stock_for(records, "missing") == 0
"""

TEST_B = """from inventory import stock_for

def test_other_sku():
    items = [{"sku": "known", "quantity": 4}]
    assert stock_for(items, "other") == 0
"""


class ClosingExecutor:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FrozenEngine:
    def __init__(self, case: Case | None = None, error: Exception | None = None):
        self.case = case
        self.error = error
        self.executor = ClosingExecutor()

    def investigate(self, claim, **kwargs):
        if self.error:
            raise self.error
        assert self.case is not None
        return self.case


def _case(index: int, code: str = TEST_A) -> Case:
    case = Case(id=f"case-{index}", mode=Mode.DETECTIVE, verdict=Verdict.PROVEN)
    case.test_file = CaseTestArtifact("test_repro.py", code)
    case.evidence = Evidence(
        fail_signature="KeyError: 'missing'",
        reruns=5,
        deterministic=True,
    )
    return case


def _study(cases: list[Case | Exception]):
    engines: list[FrozenEngine] = []

    def factory(index: int):
        item = cases[index]
        engine = (
            FrozenEngine(error=item) if isinstance(item, Exception) else FrozenEngine(case=item)
        )
        engines.append(engine)
        return engine, f"variant-{index % 2}"

    report = run_reproducibility_study(
        claim=Claim("unknown SKU should return zero", "/repo"),
        target=RepoState("/repo", "target", source="fixture/repo"),
        base=RepoState("/fixed", "base"),
        engine_factory=factory,
        runs=len(cases),
    )
    return report, engines


def test_semantic_fingerprint_ignores_formatting_test_names_and_local_names():
    assert semantic_test_fingerprint(TEST_A) == semantic_test_fingerprint(TEST_A_RENAMED)
    assert semantic_test_fingerprint(TEST_A) != semantic_test_fingerprint(TEST_B)


def test_root_cause_fingerprint_excludes_test_framework_calls():
    case = _case(
        0,
        "from inventory import stock_for\n"
        "import pytest\n"
        "def test_missing():\n"
        "    with pytest.raises(KeyError):\n"
        "        stock_for([], 'missing')\n",
    )

    report, _engines = _study([case, case])

    assert report.runs[0].root_cause_basis == ("targets=inventory.stock_for; failure=KeyError")


def test_study_reports_full_convergence_and_preserves_auditable_cases(tmp_path: Path):
    report, engines = _study([_case(0), _case(1, TEST_A_RENAMED), _case(2)])

    assert report.schema_version == SCHEMA_VERSION
    assert report.completed_runs == report.requested_runs == 3
    assert report.variants == ("variant-0", "variant-1")
    assert report.verdict.convergence == 1.0
    assert report.root_cause.convergence == 1.0
    assert report.test_semantics.convergence == 1.0
    assert report.test_semantics.coverage == 1.0
    assert report.converged
    assert report.runs[0].root_cause_basis == ("targets=inventory.stock_for; failure=KeyError")
    assert report.runs[0].case and report.runs[0].case["id"] == "case-0"
    assert all(engine.executor.closed for engine in engines)

    path = save_reproducibility_report(report, tmp_path)
    payload = json.loads(path.read_text())
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["converged"] is True
    assert payload["runs"][1]["case"]["test_file"]["code"] == TEST_A_RENAMED


def test_different_literal_inputs_are_conservatively_reported_as_divergence():
    report, _engines = _study([_case(0), _case(1, TEST_B)])

    assert report.verdict.convergence == 1.0
    assert report.root_cause.convergence == 1.0
    assert report.test_semantics.convergence == 0.5
    assert len(report.test_semantics.groups) == 2
    assert not report.converged


def test_run_error_is_recorded_and_does_not_discard_other_samples():
    report, engines = _study([_case(0), RuntimeError("model unavailable")])

    assert report.completed_runs == 1
    assert report.verdict.coverage == 0.5
    assert report.runs[1].error == "RuntimeError: model unavailable"
    assert not report.converged
    assert all(engine.executor.closed for engine in engines)


@pytest.mark.parametrize("runs", [1, 51])
def test_study_rejects_unbounded_run_counts(runs: int):
    with pytest.raises(ValueError, match="between 2 and 50"):
        run_reproducibility_study(
            claim=Claim("claim", "/repo"),
            target=RepoState("/repo", "target"),
            engine_factory=lambda _index: (FrozenEngine(_case(0)), "test"),
            runs=runs,
        )
