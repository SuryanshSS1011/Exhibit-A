"""Fixed-shape execution workflow for behavior-preserving refactor claims."""

from __future__ import annotations

from dataclasses import replace

from ..executor.base import ExecSpec, Executor, RepoState
from .refactor_check import RefactorCheckResult, refactor_check

CONTRACT_PATH = "test_refactor_contract.py"
CONTRACT_COMMAND = f"python3 -m pytest -x -q {CONTRACT_PATH}"


def run_refactor_contract(
    executor: Executor,
    base: RepoState,
    target: RepoState,
    contract_code: str,
    *,
    reruns: int = 3,
    timeout_s: int = 120,
) -> RefactorCheckResult:
    """Run one trusted contract repeatedly against pre/post-refactor states."""
    if not isinstance(reruns, int) or isinstance(reruns, bool) or reruns < 2:
        raise ValueError("reruns must be at least 2")
    if not isinstance(timeout_s, int) or isinstance(timeout_s, bool) or timeout_s < 1:
        raise ValueError("timeout_s must be positive")
    if not isinstance(contract_code, str):
        raise TypeError("contract_code must be a string")

    base = replace(base, label="base")
    target = replace(target, label="target")
    base_image = executor.prepare(base)
    target_image = executor.prepare(target)
    base_spec = _contract_spec(contract_code, timeout_s, base_image)
    target_spec = _contract_spec(contract_code, timeout_s, target_image)
    base_runs = [executor.run(base, base_spec) for _ in range(reruns)]
    target_runs = [executor.run(target, target_spec) for _ in range(reruns)]
    return refactor_check(
        base_runs=base_runs,
        target_runs=target_runs,
        required_reruns=reruns,
    )


def _contract_spec(contract_code: str, timeout_s: int, image: str | None) -> ExecSpec:
    return ExecSpec(
        test_path=CONTRACT_PATH,
        test_code=contract_code,
        command=CONTRACT_COMMAND,
        timeout_s=timeout_s,
        network=False,
        image=image,
    )
