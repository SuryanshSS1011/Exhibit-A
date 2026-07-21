"""Shrink proven pytest evidence without weakening the flip contract.

The transformations in this module are only proposals. A candidate is accepted
solely when a fresh execution clears the same deterministic ``flip_check`` that
admitted the original test. Minimization therefore has no verdict authority.
"""

from __future__ import annotations

import ast
import copy
import math
from dataclasses import dataclass
from typing import Callable, Iterator

from ..executor.base import ExecSpec, Executor, RepoState
from ..models.case import TestArtifact
from .diff_location import ChangedLines
from .flip_check import flip_check


@dataclass(frozen=True)
class MinimizationResult:
    artifact: TestArtifact
    verified: bool
    attempts: int
    accepted: int
    original_lines: int
    minimized_lines: int

    @property
    def reduction_ratio(self) -> float:
        if self.original_lines == 0:
            return 0.0
        return (self.original_lines - self.minimized_lines) / self.original_lines


Verifier = Callable[[str], bool]


def minimize_proven_test(
    *,
    executor: Executor,
    target: RepoState,
    base: RepoState,
    spec: ExecSpec,
    expected_signature: str | None,
    reruns: int,
    changed_lines: ChangedLines | None = None,
    control: RepoState | None = None,
    target_image: str | None = None,
    base_image: str | None = None,
    control_image: str | None = None,
    max_attempts: int = 32,
) -> MinimizationResult:
    """Return the smallest candidate found under a bounded, real flip oracle."""
    if reruns < 1:
        raise ValueError("minimization reruns must be positive")
    if max_attempts < 1:
        raise ValueError("minimization max_attempts must be positive")

    attempts = 0
    accepted = 0

    def verifies(code: str) -> bool:
        nonlocal attempts
        if attempts >= max_attempts or not _parses(code):
            return False
        attempts += 1
        candidate = ExecSpec(
            test_path=spec.test_path,
            test_code=code,
            command=spec.command,
            timeout_s=spec.timeout_s,
            network=spec.network,
        )
        target_spec = ExecSpec(**{**candidate.__dict__, "image": target_image})
        base_spec = ExecSpec(**{**candidate.__dict__, "image": base_image})
        control_spec = ExecSpec(**{**candidate.__dict__, "image": control_image})
        target_runs = [executor.run(target, target_spec) for _ in range(reruns)]
        base_run = executor.run(base, base_spec)
        control_run = executor.run(control, control_spec) if control is not None else None
        result = flip_check(
            target_runs=target_runs,
            base_run=base_run,
            test_code=code,
            expected_signature=expected_signature,
            changed_lines=changed_lines,
            control_run=control_run,
        )
        return result.admissible and result.tier == "flip"

    original = spec.test_code
    current = original

    current, count = _ddmin_lines(current, verifies)
    accepted += count

    while attempts < max_attempts:
        changed = False
        for proposal in _ast_proposals(current):
            if attempts >= max_attempts:
                break
            if _line_count(proposal) > _line_count(current) or len(proposal) >= len(current):
                continue
            if verifies(proposal):
                current = proposal
                accepted += 1
                changed = True
                break
        if not changed:
            break

    # The original was already proven by the caller. ``verified`` means the emitted
    # artifact itself was independently rechecked during this pass. If no shrink was
    # accepted, spend one bounded attempt confirming the unchanged artifact.
    verified = accepted > 0
    if not verified and attempts < max_attempts:
        verified = verifies(current)

    return MinimizationResult(
        artifact=TestArtifact(path=spec.test_path, code=current),
        verified=verified,
        attempts=attempts,
        accepted=accepted,
        original_lines=_line_count(original),
        minimized_lines=_line_count(current),
    )


def _ddmin_lines(code: str, verifies: Verifier) -> tuple[str, int]:
    lines = code.splitlines(keepends=True)
    if len(lines) < 2:
        return code, 0
    granularity = 2
    accepted = 0
    while len(lines) >= 2 and granularity <= len(lines):
        chunk_size = math.ceil(len(lines) / granularity)
        reduced = False
        for start in range(0, len(lines), chunk_size):
            proposal_lines = lines[:start] + lines[start + chunk_size :]
            proposal = "".join(proposal_lines)
            if proposal and verifies(proposal):
                lines = proposal_lines
                accepted += 1
                granularity = max(2, granularity - 1)
                reduced = True
                break
        if reduced:
            continue
        if granularity == len(lines):
            break
        granularity = min(len(lines), granularity * 2)
    return "".join(lines), accepted


def _ast_proposals(code: str) -> Iterator[str]:
    """Propose fixture/setup inlining and progressively smaller literal inputs."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return

    for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
        load_counts = _load_counts(function)
        for statement in function.body:
            if not (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and load_counts.get(statement.targets[0].id) == 1
                and _safe_inline_value(statement.value)
            ):
                continue
            candidate = copy.deepcopy(tree)
            yield _unparse(
                _InlineAssignment(statement.targets[0].id, statement.lineno).visit(candidate)
            )

    for node in ast.walk(tree):
        for replacement in _smaller_values(node):
            candidate = copy.deepcopy(tree)
            yield _unparse(_ReplaceNode(_node_key(node), replacement).visit(candidate))


def _load_counts(node: ast.AST) -> dict[str, int]:
    counts: dict[str, int] = {}
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            counts[child.id] = counts.get(child.id, 0) + 1
    return counts


def _safe_inline_value(node: ast.AST) -> bool:
    return isinstance(node, (ast.Constant, ast.List, ast.Tuple, ast.Set, ast.Dict))


class _InlineAssignment(ast.NodeTransformer):
    def __init__(self, name: str, line: int):
        self.name = name
        self.line = line
        self.value: ast.expr | None = None

    def visit_Assign(self, node: ast.Assign) -> ast.AST | None:
        if (
            node.lineno == self.line
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == self.name
        ):
            self.value = copy.deepcopy(node.value)
            return None
        return self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if isinstance(node.ctx, ast.Load) and node.id == self.name and self.value is not None:
            return ast.copy_location(copy.deepcopy(self.value), node)
        return node


NodeKey = tuple[type[ast.AST], int, int, int, int]


def _node_key(node: ast.AST) -> NodeKey:
    return (
        type(node),
        getattr(node, "lineno", -1),
        getattr(node, "col_offset", -1),
        getattr(node, "end_lineno", -1),
        getattr(node, "end_col_offset", -1),
    )


class _ReplaceNode(ast.NodeTransformer):
    def __init__(self, key: NodeKey, replacement: ast.expr):
        self.key = key
        self.replacement = replacement

    def generic_visit(self, node: ast.AST) -> ast.AST:
        if _node_key(node) == self.key:
            return ast.copy_location(copy.deepcopy(self.replacement), node)
        return super().generic_visit(node)


def _smaller_values(node: ast.AST) -> Iterator[ast.expr]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and len(node.elts) > 1:
        for index in range(len(node.elts)):
            smaller = copy.deepcopy(node)
            smaller.elts.pop(index)
            yield smaller
    elif isinstance(node, ast.Dict) and len(node.keys) > 1:
        for index in range(len(node.keys)):
            smaller = copy.deepcopy(node)
            smaller.keys.pop(index)
            smaller.values.pop(index)
            yield smaller
    elif isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) > 1:
        yield ast.Constant(node.value[:1])
        yield ast.Constant("")
    elif (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
        and node.value not in {-1, 0, 1}
    ):
        yield ast.Constant(0)
        yield ast.Constant(1 if node.value > 0 else -1)


def _unparse(tree: ast.AST) -> str:
    ast.fix_missing_locations(tree)
    return f"{ast.unparse(tree)}\n"


def _parses(code: str) -> bool:
    try:
        ast.parse(code)
    except SyntaxError:
        return False
    return True


def _line_count(code: str) -> int:
    return len(code.splitlines())
