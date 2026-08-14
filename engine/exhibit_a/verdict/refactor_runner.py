"""Fixed-shape execution workflow for behavior-preserving refactor claims."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

from ..connectors import (
    EvidenceProvenance,
    LocalTestConnector,
    LocalTestRequest,
    collect_validated_local_test,
)
from ..executor.base import ExecOutcome, ExecSpec, Executor, RepoState
from .refactor_check import RefactorCheckResult, refactor_check

CONTRACT_PATH = "test_refactor_contract.py"
CONTRACT_COMMAND = f"python3 -m pytest -x -q {CONTRACT_PATH}"
EVIDENCE_SCHEMA = "behavior-refactor-evidence/v2"
CLAIM_TYPE = "behavior_preserving_refactor"


@dataclass(frozen=True)
class RefactorRunEvidence:
    evidence_id: str
    state: str
    ordinal: int
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: int | float
    image: str | None


@dataclass(frozen=True)
class RefactorEvidence:
    schema_version: str
    claim_type: str
    contract_path: str
    contract_code: str
    command: str
    contract_sha256: str
    reruns: int
    timeout_s: int
    result: RefactorCheckResult
    runs: tuple[RefactorRunEvidence, ...]
    evidence_sources: tuple[EvidenceProvenance, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return the complete machine-readable evidence object."""
        return asdict(self)


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
    return collect_refactor_evidence(
        executor,
        base,
        target,
        contract_code,
        reruns=reruns,
        timeout_s=timeout_s,
    ).result


def collect_refactor_evidence(
    executor: Executor,
    base: RepoState,
    target: RepoState,
    contract_code: str,
    *,
    reruns: int = 3,
    timeout_s: int = 120,
) -> RefactorEvidence:
    """Execute, validate, and bind every refactor run to a provenance receipt."""
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
    connector = LocalTestConnector(executor)
    base_outputs = tuple(
        collect_validated_local_test(connector, LocalTestRequest(base, base_spec))
        for _ in range(reruns)
    )
    target_outputs = tuple(
        collect_validated_local_test(connector, LocalTestRequest(target, target_spec))
        for _ in range(reruns)
    )
    outputs = base_outputs + target_outputs
    result = refactor_check(
        base_runs=[output.payload for output in base_outputs],
        target_runs=[output.payload for output in target_outputs],
        required_reruns=reruns,
    )
    sources = tuple(output.provenance for output in outputs)
    return RefactorEvidence(
        schema_version=EVIDENCE_SCHEMA,
        claim_type=CLAIM_TYPE,
        contract_path=CONTRACT_PATH,
        contract_code=contract_code,
        command=CONTRACT_COMMAND,
        contract_sha256=sources[0].artifact_sha256,
        reruns=reruns,
        timeout_s=timeout_s,
        result=result,
        runs=tuple(
            _run_evidence(
                output.provenance,
                output.payload,
                index,
                reruns,
                base_image,
                target_image,
            )
            for index, output in enumerate(outputs)
        ),
        evidence_sources=sources,
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


def _run_evidence(
    provenance: EvidenceProvenance,
    outcome: ExecOutcome,
    index: int,
    reruns: int,
    base_image: str | None,
    target_image: str | None,
) -> RefactorRunEvidence:
    state = "base" if index < reruns else "target"
    ordinal = index + 1 if state == "base" else index - reruns + 1
    return RefactorRunEvidence(
        evidence_id=provenance.evidence_id,
        state=state,
        ordinal=ordinal,
        exit_code=outcome.exit_code,
        stdout=outcome.stdout,
        stderr=outcome.stderr,
        timed_out=outcome.timed_out,
        duration_s=outcome.duration_s,
        image=base_image if state == "base" else target_image,
    )
