"""Transparent evidence-strength scoring layered above the raw verdict.

The scalar ranks already-admitted evidence. It cannot admit a Case and deliberately
reports coverage separately so unavailable measurements are never mistaken for zero.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path, PurePosixPath
from typing import Iterable

from ..models.case import (
    EvidenceMinimization,
    EvidenceStrength,
    StrengthComponent,
)
from .mutation_testing import MutationScore

SCHEMA_VERSION = "evidence-strength/v1"
_WEIGHTS = {
    "mutation": 0.30,
    "signature": 0.20,
    "determinism": 0.20,
    "minimality": 0.15,
    "surface_distance": 0.15,
}
_FILE_FRAME = re.compile(r'^\s*File "(?P<path>[^"]+)", line (?P<line>\d+)', re.M)
_PYTEST_FRAME = re.compile(r"^(?P<path>[^\s:][^:]*\.py):(?P<line>\d+)(?::|\s)", re.M)


def compute_evidence_strength(
    *,
    fail_signature: str | None,
    deterministic: bool,
    reruns: int,
    minimization: EvidenceMinimization | None,
    mutation_score: MutationScore | None,
    source_frames: Iterable[str] = (),
    root_cause_paths: Iterable[str] = (),
) -> EvidenceStrength:
    """Compute a versioned weighted mean over the measurements that are available."""
    mutation = _mutation_component(mutation_score)
    signature = _signature_component(fail_signature)
    determinism = _determinism_component(deterministic, reruns)
    minimality = _minimality_component(minimization)
    surface_distance = _surface_component(source_frames, root_cause_paths)
    components = (mutation, signature, determinism, minimality, surface_distance)
    available = [component for component in components if component.score is not None]
    available_weight = sum(component.weight for component in available)
    composite = (
        sum(component.score * component.weight for component in available) / available_weight
        if available_weight
        else 0.0
    )
    return EvidenceStrength(
        schema_version=SCHEMA_VERSION,
        composite=_round(composite),
        coverage=_round(available_weight / sum(_WEIGHTS.values())),
        mutation=mutation,
        signature=signature,
        determinism=determinism,
        minimality=minimality,
        surface_distance=surface_distance,
    )


def traceback_source_paths(log: str, repo_path: str, test_path: str) -> tuple[str, ...]:
    """Extract ordered, repository-relative Python source frames from pytest output."""
    root = Path(repo_path).resolve()
    generated_test = PurePosixPath(test_path).as_posix()
    matches = [
        (match.start(), match.group("path"))
        for pattern in (_FILE_FRAME, _PYTEST_FRAME)
        for match in pattern.finditer(log)
    ]
    raw_paths = [path for _offset, path in sorted(matches)]
    found: list[str] = []
    for raw_path in raw_paths:
        relative = _repo_relative_path(root, raw_path)
        if relative is None or relative == generated_test or relative in found:
            continue
        found.append(relative)
    return tuple(found)


def imported_source_paths(test_code: str, repo_path: str) -> tuple[str, ...]:
    """Resolve absolute Python imports in a generated test to local source files."""
    root = Path(repo_path).resolve()
    try:
        tree = ast.parse(test_code)
    except SyntaxError:
        return ()
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
    found: list[str] = []
    for module in modules:
        parts = module.split(".")
        candidates = (
            root.joinpath(*parts).with_suffix(".py"),
            root.joinpath(*parts, "__init__.py"),
        )
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            if resolved.is_file() and resolved.is_relative_to(root):
                relative = resolved.relative_to(root).as_posix()
                if relative not in found:
                    found.append(relative)
                break
    return tuple(found)


def _mutation_component(score: MutationScore | None) -> StrengthComponent:
    weight = _WEIGHTS["mutation"]
    if score is None:
        return StrengthComponent(None, weight, "mutation surface unavailable")
    if score.kill_rate is None:
        reason = score.reason or (
            f"no eligible mutants ({score.generated} generated, {score.invalid} invalid)"
        )
        return StrengthComponent(None, weight, reason)
    return StrengthComponent(
        _round(score.kill_rate),
        weight,
        f"{score.killed}/{score.eligible} eligible mutants killed; "
        f"{score.invalid} invalid excluded",
    )


def _signature_component(signature: str | None) -> StrengthComponent:
    weight = _WEIGHTS["signature"]
    if not signature:
        return StrengthComponent(None, weight, "no extractable failure signature")
    exception, separator, detail = signature.partition(":")
    is_assertion = exception.strip().endswith("AssertionError")
    has_detail = bool(separator and detail.strip())
    if is_assertion:
        score = 0.65 if has_detail else 0.40
        label = "assertion with expression detail" if has_detail else "bare assertion"
    else:
        score = 1.0 if has_detail else 0.80
        label = "typed exception with value detail" if has_detail else "typed exception"
    return StrengthComponent(score, weight, f"{label}: {signature}")


def _determinism_component(deterministic: bool, reruns: int) -> StrengthComponent:
    weight = _WEIGHTS["determinism"]
    bounded_reruns = max(0, reruns)
    score = min(bounded_reruns / 5, 1.0) if deterministic else 0.0
    state = "stable" if deterministic else "not stable"
    return StrengthComponent(
        _round(score),
        weight,
        f"{state} across {bounded_reruns} target rerun(s); full credit at 5",
    )


def _minimality_component(
    minimization: EvidenceMinimization | None,
) -> StrengthComponent:
    weight = _WEIGHTS["minimality"]
    if minimization is None or not minimization.verified:
        return StrengthComponent(None, weight, "no independently verified minimized artifact")
    lines = max(1, minimization.minimized_lines)
    score = min(6 / lines, 1.0)
    return StrengthComponent(
        _round(score),
        weight,
        f"{lines} minimized lines from {minimization.original_lines}; full credit at 6 or fewer",
    )


def _surface_component(
    source_frames: Iterable[str], root_cause_paths: Iterable[str]
) -> StrengthComponent:
    weight = _WEIGHTS["surface_distance"]
    frames = tuple(dict.fromkeys(source_frames))
    if not frames:
        return StrengthComponent(None, weight, "no repository source frame in failure trace")
    roots = set(root_cause_paths)
    root_index = max(
        (index for index, frame in enumerate(frames) if not roots or frame in roots),
        default=-1,
    )
    if root_index < 0:
        return StrengthComponent(
            None, weight, "failure trace does not reach the selected root cause"
        )
    intervening_frames = root_index
    score = 1 / (1 + intervening_frames)
    return StrengthComponent(
        _round(score),
        weight,
        f"{intervening_frames} repository frame(s) between test surface and root cause",
    )


def _repo_relative_path(root: Path, raw_path: str) -> str | None:
    normalized = raw_path.replace("\\", "/").removeprefix("./")
    path = Path(normalized)
    if path.is_absolute():
        try:
            candidate = path.resolve(strict=True)
        except OSError:
            return None
        if candidate.is_file() and candidate.is_relative_to(root):
            return candidate.relative_to(root).as_posix()
        return None
    parts = PurePosixPath(normalized).parts
    for index in range(len(parts)):
        candidate = root.joinpath(*parts[index:])
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file() and resolved.is_relative_to(root):
            return resolved.relative_to(root).as_posix()
    return None


def _round(value: float) -> float:
    return round(max(0.0, min(value, 1.0)), 6)
