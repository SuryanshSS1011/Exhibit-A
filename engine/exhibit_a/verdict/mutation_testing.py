"""Deterministic mutation scores layered above, never inside, the flip verdict."""

from __future__ import annotations

import io
import ast
import tokenize
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping

from ..executor.base import ExecOutcome, ExecSpec, Executor, RepoState, SourceMutation
from .flip_check import detect_infra_failure, extract_signature

_REPLACEMENTS = {
    "==": "!=",
    "!=": "==",
    "<": "<=",
    "<=": "<",
    ">": ">=",
    ">=": ">",
    "+": "-",
    "-": "+",
    "*": "//",
    "//": "*",
    "%": "*",
    "True": "False",
    "False": "True",
}


class MutationStatus(str, Enum):
    KILLED = "killed"
    SURVIVED = "survived"
    INVALID = "invalid"


@dataclass(frozen=True)
class MutationRun:
    exit_code: int
    passed: bool
    signature: str | None
    log: str
    timed_out: bool
    duration_s: float


@dataclass(frozen=True)
class MutationResult:
    mutation: SourceMutation
    status: MutationStatus
    runs: tuple[MutationRun, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class MutationScore:
    """A descriptive score only; it has no authority over evidence admission."""

    baseline_passed: bool
    baseline_runs: tuple[MutationRun, ...]
    generated: int
    eligible: int
    killed: int
    survived: int
    invalid: int
    kill_rate: float | None
    results: tuple[MutationResult, ...] = field(default_factory=tuple)
    reason: str | None = None


def discover_mutations(
    repo_path: str | Path,
    source_paths: Iterable[str],
    *,
    suspect_lines: Mapping[str, set[int]] | None = None,
    limit: int = 128,
) -> tuple[SourceMutation, ...]:
    """Discover deterministic, syntax-preserving token mutants without editing source."""
    if limit < 1:
        raise ValueError("mutation limit must be positive")
    root = Path(repo_path).resolve()
    if not root.is_dir():
        raise ValueError(f"mutation repository does not exist: {root}")
    mutations: list[SourceMutation] = []
    for source_path in sorted(set(source_paths)):
        relative = _safe_source_path(source_path)
        try:
            target = root.joinpath(*relative.parts).resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValueError(f"mutation source does not exist: {source_path}") from exc
        if not target.is_relative_to(root) or not target.is_file():
            raise ValueError(f"mutation source escapes repository: {source_path}")
        selected_lines = suspect_lines.get(source_path) if suspect_lines is not None else None
        try:
            source = target.read_text()
            ast.parse(source, filename=relative.as_posix())
            tokens = tokenize.generate_tokens(io.StringIO(source).readline)
            for token in tokens:
                replacement = _REPLACEMENTS.get(token.string)
                if replacement is None or token.type not in {tokenize.OP, tokenize.NAME}:
                    continue
                if selected_lines is not None and token.start[0] not in selected_lines:
                    continue
                mutation_id = (
                    f"{relative.as_posix()}:{token.start[0]}:{token.start[1]}:"
                    f"{token.string}->{replacement}"
                )
                mutation = SourceMutation(
                    id=mutation_id,
                    path=relative.as_posix(),
                    line=token.start[0],
                    start_col=token.start[1],
                    end_col=token.end[1],
                    original=token.string,
                    replacement=replacement,
                )
                try:
                    ast.parse(_mutated_source(source, mutation), filename=relative.as_posix())
                except SyntaxError:
                    continue
                mutations.append(mutation)
                if len(mutations) >= limit:
                    return tuple(mutations)
        except (SyntaxError, UnicodeError, tokenize.TokenError) as exc:
            raise ValueError(f"cannot tokenize mutation source {source_path}: {exc}") from exc
    return tuple(mutations)


def score_mutations(
    executor: Executor,
    repo: RepoState,
    spec: ExecSpec,
    mutations: Iterable[SourceMutation],
    *,
    reruns: int = 3,
) -> MutationScore:
    """Measure which mutants a passing candidate kills, with deterministic reruns."""
    if reruns < 1:
        raise ValueError("mutation reruns must be positive")
    selected = tuple(mutations)
    baseline_outcomes = tuple(executor.run(repo, spec) for _ in range(reruns))
    baseline_runs = tuple(_record(outcome) for outcome in baseline_outcomes)
    if not all(outcome.passed for outcome in baseline_outcomes):
        return MutationScore(
            baseline_passed=False,
            baseline_runs=baseline_runs,
            generated=len(selected),
            eligible=0,
            killed=0,
            survived=0,
            invalid=len(selected),
            kill_rate=None,
            reason="candidate does not pass deterministically on the mutation baseline",
        )

    results = tuple(_score_one(executor, repo, spec, mutation, reruns) for mutation in selected)
    killed = sum(result.status is MutationStatus.KILLED for result in results)
    survived = sum(result.status is MutationStatus.SURVIVED for result in results)
    invalid = sum(result.status is MutationStatus.INVALID for result in results)
    eligible = killed + survived
    return MutationScore(
        baseline_passed=True,
        baseline_runs=baseline_runs,
        generated=len(selected),
        eligible=eligible,
        killed=killed,
        survived=survived,
        invalid=invalid,
        kill_rate=killed / eligible if eligible else None,
        results=results,
    )


def _score_one(
    executor: Executor,
    repo: RepoState,
    spec: ExecSpec,
    mutation: SourceMutation,
    reruns: int,
) -> MutationResult:
    outcomes: list[ExecOutcome] = []
    for _ in range(reruns):
        outcome = executor.run_mutant(repo, spec, mutation)
        if outcome is None:
            return MutationResult(
                mutation,
                MutationStatus.INVALID,
                reason="executor does not support disposable source mutation",
            )
        outcomes.append(outcome)
    runs = tuple(_record(outcome) for outcome in outcomes)
    if any(outcome.timed_out for outcome in outcomes):
        return MutationResult(mutation, MutationStatus.INVALID, runs, "mutant timed out")
    passed = [outcome.passed for outcome in outcomes]
    if any(passed) and not all(passed):
        return MutationResult(
            mutation,
            MutationStatus.INVALID,
            runs,
            "mutant result was nondeterministic",
        )
    if all(passed):
        return MutationResult(mutation, MutationStatus.SURVIVED, runs)
    infra = next(
        (reason for outcome in outcomes if (reason := detect_infra_failure(outcome)) is not None),
        None,
    )
    if infra:
        return MutationResult(mutation, MutationStatus.INVALID, runs, infra)
    signatures = {extract_signature(outcome) for outcome in outcomes}
    if None in signatures or len(signatures) != 1:
        return MutationResult(
            mutation,
            MutationStatus.INVALID,
            runs,
            "mutant failure signature was missing or nondeterministic",
        )
    return MutationResult(mutation, MutationStatus.KILLED, runs)


def _record(outcome: ExecOutcome) -> MutationRun:
    return MutationRun(
        exit_code=outcome.exit_code,
        passed=outcome.passed,
        signature=extract_signature(outcome),
        log=outcome.log,
        timed_out=outcome.timed_out,
        duration_s=outcome.duration_s,
    )


def _safe_source_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or path.suffix != ".py":
        raise ValueError(f"unsafe mutation source path: {value!r}")
    return path


def _mutated_source(source: str, mutation: SourceMutation) -> str:
    lines = source.splitlines(keepends=True)
    line = lines[mutation.line - 1]
    lines[mutation.line - 1] = (
        line[: mutation.start_col] + mutation.replacement + line[mutation.end_col :]
    )
    return "".join(lines)
