"""Execution-verified escalation from a concrete flip to a generalized property."""

from __future__ import annotations

import ast
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from ..engine import candidate_policy_reason
from ..executor.base import ExecSpec, Executor, RepoState
from ..hypothesis.property import PropertyCandidate, PropertyGenerator
from ..store.suite_gap import ENGINE_VERSION
from ..verdict.flip_check import flip_check
from ..verdict.mutation_testing import discover_mutations, score_mutations

SCHEMA_VERSION = "property-escalation/v1"


class PropertyStatus(str, Enum):
    VERIFIED = "verified_property_flip"
    REJECTED = "rejected_property"
    DECLINED = "no_justified_property"


@dataclass(frozen=True)
class PropertyEscalationReport:
    schema_version: str
    engine_version: str
    id: str
    created_at: str
    case_id: str
    status: PropertyStatus
    property_kind: str | None
    domain: str | None
    test_path: str | None
    test_code: str | None
    target_passes: tuple[bool, ...]
    base_passes: tuple[bool, ...]
    fail_signature: str | None
    mutation_kill_rate: float | None
    concrete_strength_score: float | None
    reason: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def run_property_escalation(
    case_path: str | Path,
    target_path: str | Path,
    base_path: str | Path,
    generator: PropertyGenerator,
    executor: Executor,
    *,
    source_paths: list[str] | None = None,
    reruns: int = 3,
    max_mutants: int = 32,
) -> PropertyEscalationReport:
    if reruns < 1:
        raise ValueError("property reruns must be positive")
    case = _load_case(case_path)
    target = RepoState(str(Path(target_path).resolve()), "target", source=str(target_path))
    base = RepoState(str(Path(base_path).resolve()), "base", source=str(base_path))
    if not Path(target.path).is_dir() or not Path(base.path).is_dir():
        raise ValueError("property escalation requires target and base directories")
    proposal = generator.propose(case, target.path)
    if proposal is None:
        return _report(case, PropertyStatus.DECLINED, reason="generator declined")
    reason = candidate_policy_reason(proposal.candidate)
    if reason:
        return _report(case, PropertyStatus.REJECTED, proposal, reason=reason)
    kind = property_kind(proposal.candidate.test_code)
    if kind is None:
        return _report(
            case,
            PropertyStatus.REJECTED,
            proposal,
            reason="candidate has neither Hypothesis @given nor >=3 parametrized examples",
        )
    spec = ExecSpec(
        proposal.candidate.test_path,
        proposal.candidate.test_code,
        proposal.candidate.run_command,
    )
    mutation_rate = None
    try:
        target_image = executor.prepare(target)
        base_image = executor.prepare(base)
        target_spec = _with_image(spec, target_image)
        base_spec = _with_image(spec, base_image)
        target_runs = tuple(executor.run(target, target_spec) for _ in range(reruns))
        base_runs = tuple(executor.run(base, base_spec) for _ in range(reruns))
        result = flip_check(
            test_code=spec.test_code,
            target_runs=list(target_runs),
            base_run=base_runs[0],
            expected_signature=proposal.candidate.expected_signature,
        )
        if not all(run.passed for run in base_runs):
            result = type(result)(False, "property did not pass deterministically on base")
        if result.admissible and source_paths:
            mutations = discover_mutations(base.path, source_paths, limit=max_mutants)
            mutation_rate = score_mutations(
                executor, base, base_spec, mutations, reruns=reruns
            ).kill_rate
        return _report(
            case,
            PropertyStatus.VERIFIED if result.admissible else PropertyStatus.REJECTED,
            proposal,
            kind=kind,
            target=tuple(run.passed for run in target_runs),
            base=tuple(run.passed for run in base_runs),
            signature=result.fail_signature,
            mutation_rate=mutation_rate,
            reason=result.reason,
        )
    finally:
        executor.close()


def save_property_report(report: PropertyEscalationReport, root: str | Path) -> Path:
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{report.id}.json"
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return path


def property_kind(code: str) -> str | None:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and _name(decorator.func).endswith("given"):
                return "hypothesis"
            if (
                isinstance(decorator, ast.Call)
                and _name(decorator.func).endswith("parametrize")
                and len(decorator.args) >= 2
                and isinstance(decorator.args[1], (ast.List, ast.Tuple))
                and len(decorator.args[1].elts) >= 3
            ):
                return "pytest-parametrize"
    return None


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value)}.{node.attr}"
    return ""


def _load_case(path: str | Path) -> dict:
    case = json.loads(Path(path).read_text())
    if case.get("verdict") != "PROVEN" or not isinstance(case.get("test_file"), dict):
        raise ValueError("property escalation requires a sealed PROVEN Case")
    return case


def _with_image(spec: ExecSpec, image: str | None) -> ExecSpec:
    return ExecSpec(spec.test_path, spec.test_code, spec.command, spec.timeout_s, image=image)


def _report(
    case: dict,
    status: PropertyStatus,
    proposal: PropertyCandidate | None = None,
    *,
    kind: str | None = None,
    target: tuple[bool, ...] = (),
    base: tuple[bool, ...] = (),
    signature: str | None = None,
    mutation_rate: float | None = None,
    reason: str | None = None,
) -> PropertyEscalationReport:
    strength = case.get("evidence_strength") or {}
    return PropertyEscalationReport(
        SCHEMA_VERSION,
        ENGINE_VERSION,
        uuid.uuid4().hex[:12],
        datetime.now(timezone.utc).isoformat(),
        str(case.get("id", "unknown")),
        status,
        kind,
        proposal.domain if proposal else None,
        proposal.candidate.test_path if proposal else None,
        proposal.candidate.test_code if proposal else None,
        target,
        base,
        signature,
        mutation_rate,
        strength.get("score"),
        reason,
    )
