"""Repeated-run convergence study for generated bug reproductions.

This study observes engine outcomes; it has no evidence-admission authority. Its
semantic fingerprint is intentionally conservative: formatting, test names, and
local variable names are normalized, while literal inputs and assertion structure
are preserved so superficially similar boundary cases are not merged.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..engine import EvidenceEngine
from ..executor.base import RepoState
from ..hypothesis.generator import Claim
from ..models.case import Case, Mode
from ..store.suite_gap import ENGINE_VERSION

SCHEMA_VERSION = "reproducibility-study/v1"
_INERT_IMPORT_ROOTS = {"pytest", "unittest", "sys", "os", "re", "math", "json", "typing"}


@dataclass(frozen=True)
class ConvergenceMetric:
    convergence: float | None
    coverage: float
    groups: dict[str, int]
    basis: str


@dataclass(frozen=True)
class StudyRun:
    index: int
    variant: str
    case: dict | None
    verdict: str | None
    root_cause_fingerprint: str | None
    root_cause_basis: str | None
    test_semantic_fingerprint: str | None
    error: str | None


@dataclass(frozen=True)
class ReproducibilityReport:
    schema_version: str
    engine_version: str
    id: str
    created_at: str
    claim_text: str
    repo: str
    requested_runs: int
    completed_runs: int
    variants: tuple[str, ...]
    verdict: ConvergenceMetric
    root_cause: ConvergenceMetric
    test_semantics: ConvergenceMetric
    converged: bool
    runs: tuple[StudyRun, ...]

    def to_dict(self) -> dict:
        return asdict(self)


EngineFactory = Callable[[int], tuple[EvidenceEngine, str]]


def run_reproducibility_study(
    *,
    claim: Claim,
    target: RepoState,
    engine_factory: EngineFactory,
    runs: int = 5,
    base: RepoState | None = None,
    control: RepoState | None = None,
    mode: Mode = Mode.DETECTIVE,
    repo_source: str | None = None,
) -> ReproducibilityReport:
    """Run the same claim K times and report convergence without judging evidence."""
    if runs < 2 or runs > 50:
        raise ValueError("reproducibility runs must be between 2 and 50")
    records: list[StudyRun] = []
    variants: list[str] = []
    for index in range(runs):
        engine: EvidenceEngine | None = None
        variant = f"run-{index}"
        try:
            engine, variant = engine_factory(index)
            variants.append(variant)
            case = engine.investigate(
                claim,
                mode=mode,
                target=target,
                base=base,
                control=control,
                repo_source=repo_source,
            )
            records.append(_record_case(index, variant, case))
        except Exception as exc:
            if variant not in variants:
                variants.append(variant)
            records.append(
                StudyRun(
                    index=index,
                    variant=variant,
                    case=None,
                    verdict=None,
                    root_cause_fingerprint=None,
                    root_cause_basis=None,
                    test_semantic_fingerprint=None,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
        finally:
            if engine is not None:
                engine.executor.close()

    completed = sum(record.case is not None for record in records)
    verdict = _metric(
        [record.verdict for record in records],
        runs,
        "same terminal verdict across completed runs",
    )
    root_cause = _metric(
        [record.root_cause_fingerprint for record in records],
        runs,
        "same exercised import target and failure type across runs with executable evidence",
    )
    test_semantics = _metric(
        [record.test_semantic_fingerprint for record in records],
        runs,
        "same alpha-normalized test AST with literal inputs preserved",
    )
    converged = bool(
        completed == runs
        and verdict.convergence == 1.0
        and root_cause.coverage > 0
        and root_cause.convergence == 1.0
        and test_semantics.coverage > 0
        and test_semantics.convergence == 1.0
    )
    return ReproducibilityReport(
        schema_version=SCHEMA_VERSION,
        engine_version=ENGINE_VERSION,
        id=uuid.uuid4().hex[:12],
        created_at=datetime.now(timezone.utc).isoformat(),
        claim_text=claim.text,
        repo=repo_source or target.source or target.path,
        requested_runs=runs,
        completed_runs=completed,
        variants=tuple(dict.fromkeys(variants)),
        verdict=verdict,
        root_cause=root_cause,
        test_semantics=test_semantics,
        converged=converged,
        runs=tuple(records),
    )


def semantic_test_fingerprint(test_code: str) -> str:
    """Hash a conservative semantic normal form for one Python test artifact."""
    tree = ast.parse(test_code)
    normalized = _AlphaNormalizer().visit(copy.deepcopy(tree))
    ast.fix_missing_locations(normalized)
    canonical = ast.dump(normalized, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def root_cause_fingerprint(case: Case) -> tuple[str, str] | None:
    """Fingerprint the exercised imported target plus observed failure type."""
    if case.test_file is None or not case.is_evidence():
        return None
    try:
        targets = _called_import_targets(case.test_file.code)
    except SyntaxError:
        return None
    signature_type = (case.evidence.fail_signature or "unknown").split(":", 1)[0].strip()
    if not targets:
        return None
    basis = f"targets={','.join(targets)}; failure={signature_type}"
    return hashlib.sha256(basis.encode()).hexdigest(), basis


def save_reproducibility_report(report: ReproducibilityReport, root: str | Path) -> Path:
    """Persist the private, versioned study record as auditable JSON."""
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{report.id}.json"
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return path


def _record_case(index: int, variant: str, case: Case) -> StudyRun:
    root = root_cause_fingerprint(case)
    semantic = None
    if case.test_file is not None and case.is_evidence():
        try:
            semantic = semantic_test_fingerprint(case.test_file.code)
        except SyntaxError:
            pass
    return StudyRun(
        index=index,
        variant=variant,
        case=case.to_dict(),
        verdict=case.verdict.value,
        root_cause_fingerprint=root[0] if root else None,
        root_cause_basis=root[1] if root else None,
        test_semantic_fingerprint=semantic,
        error=None,
    )


def _metric(values: list[str | None], requested: int, basis: str) -> ConvergenceMetric:
    measured = [value for value in values if value is not None]
    groups = dict(sorted(Counter(measured).items()))
    convergence = max(groups.values()) / len(measured) if measured else None
    return ConvergenceMetric(
        convergence=round(convergence, 6) if convergence is not None else None,
        coverage=round(len(measured) / requested, 6),
        groups=groups,
        basis=basis,
    )


def _called_import_targets(test_code: str) -> tuple[str, ...]:
    tree = ast.parse(test_code)
    imports: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                imports[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name.split(".")[0]] = alias.name
    targets: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in imports:
            target = imports[node.func.id]
            if target.split(".", 1)[0] not in _INERT_IMPORT_ROOTS:
                targets.add(target)
        elif isinstance(node.func, ast.Attribute):
            root, suffix = _attribute_parts(node.func)
            if root in imports:
                target = f"{imports[root]}.{'.'.join(suffix)}"
                if target.split(".", 1)[0] not in _INERT_IMPORT_ROOTS:
                    targets.add(target)
    return tuple(sorted(targets))


def _attribute_parts(node: ast.Attribute) -> tuple[str, list[str]]:
    suffix = [node.attr]
    value = node.value
    while isinstance(value, ast.Attribute):
        suffix.append(value.attr)
        value = value.value
    return (value.id if isinstance(value, ast.Name) else ""), list(reversed(suffix))


class _AlphaNormalizer(ast.NodeTransformer):
    def __init__(self):
        self.scopes: list[dict[str, str]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        node.name = "test"
        mapping = {
            argument.arg: f"arg{index}"
            for index, argument in enumerate(
                [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            )
        }
        self.scopes.append(mapping)
        node = self.generic_visit(node)
        self.scopes.pop()
        return node

    def visit_arg(self, node: ast.arg) -> ast.AST:
        if self.scopes and node.arg in self.scopes[-1]:
            node.arg = self.scopes[-1][node.arg]
        return node

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if not self.scopes:
            return node
        mapping = self.scopes[-1]
        if isinstance(node.ctx, ast.Store) and node.id not in mapping:
            mapping[node.id] = f"local{len(mapping)}"
        if node.id in mapping:
            node.id = mapping[node.id]
        return node
