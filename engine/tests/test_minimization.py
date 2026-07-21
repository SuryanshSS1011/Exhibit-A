from __future__ import annotations

import sys
from pathlib import Path

from exhibit_a.executor.base import ExecSpec, RepoState
from exhibit_a.executor.local_exec import LocalExecutor
from exhibit_a.verdict.flip_check import flip_check
from exhibit_a.verdict.minimization import minimize_proven_test

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

VERBOSE_TEST = """from inventory import stock_for

# This setup is intentionally verbose so the post-verdict pass has work to do.
def test_unknown_sku_has_zero_stock():
    items = [
        {"sku": "known", "quantity": 4},
        {"sku": "extra", "quantity": 9},
    ]
    missing_sku = "missing"
    result = stock_for(items, missing_sku)
    assert result == 0
"""


def test_minimizer_emits_a_smaller_artifact_that_still_clears_flip_check():
    executor = LocalExecutor()
    target = RepoState(path=str(FIXTURES / "buggy_inventory"), label="target")
    base = RepoState(path=str(FIXTURES / "fixed_inventory"), label="base")
    spec = ExecSpec(
        test_path="test_inventory_minimized.py",
        test_code=VERBOSE_TEST,
        command=f"{sys.executable} -m pytest -x -q test_inventory_minimized.py",
    )

    result = minimize_proven_test(
        executor=executor,
        target=target,
        base=base,
        spec=spec,
        expected_signature="KeyError",
        reruns=2,
        max_attempts=24,
    )

    assert result.verified
    assert result.accepted > 0
    assert result.minimized_lines < result.original_lines
    assert result.reduction_ratio > 0
    assert result.artifact.code != VERBOSE_TEST

    minimized_spec = ExecSpec(**{**spec.__dict__, "test_code": result.artifact.code})
    target_runs = [executor.run(target, minimized_spec) for _ in range(2)]
    base_run = executor.run(base, minimized_spec)
    final_flip = flip_check(
        target_runs=target_runs,
        base_run=base_run,
        test_code=result.artifact.code,
        expected_signature="KeyError",
    )
    assert final_flip.admissible
    assert final_flip.tier == "flip"


def test_minimizer_respects_attempt_budget():
    executor = LocalExecutor()
    target = RepoState(path=str(FIXTURES / "buggy_inventory"), label="target")
    base = RepoState(path=str(FIXTURES / "fixed_inventory"), label="base")
    spec = ExecSpec(
        test_path="test_inventory_budget.py",
        test_code=VERBOSE_TEST,
        command=f"{sys.executable} -m pytest -x -q test_inventory_budget.py",
    )

    result = minimize_proven_test(
        executor=executor,
        target=target,
        base=base,
        spec=spec,
        expected_signature="KeyError",
        reruns=1,
        max_attempts=3,
    )

    assert result.attempts <= 3
