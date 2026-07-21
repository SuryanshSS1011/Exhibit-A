"""Mutation-based oracle-gap measurements for resolved benchmark instances."""

from __future__ import annotations

import json
import re
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from ..executor.base import ExecSpec, Executor, RepoState
from ..store.suite_gap import ENGINE_VERSION
from ..verdict.mutation_testing import MutationScore, discover_mutations, score_mutations

SCHEMA_VERSION = "oracle-gap/v1"
MANIFEST_VERSION = "swe-bench-oracle-corpus/v1"
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")


@dataclass(frozen=True)
class OracleInstance:
    instance_id: str
    resolved: RepoState
    test_path: str
    test_code: str
    command: str
    source_paths: tuple[str, ...]
    suspect_lines: dict[str, set[int]] | None


@dataclass(frozen=True)
class OracleItem:
    instance_id: str
    baseline_passed: bool
    generated: int
    eligible: int
    killed: int
    survived: int
    invalid: int
    kill_rate: float | None
    oracle_gap: float | None
    reason: str | None
    survivors: tuple[str, ...]


@dataclass(frozen=True)
class OracleGapReport:
    schema_version: str
    engine_version: str
    id: str
    created_at: str
    corpus: str
    instances: int
    evaluated_instances: int
    generated: int
    eligible: int
    killed: int
    survived: int
    invalid: int
    kill_rate: float | None
    oracle_gap: float | None
    items: tuple[OracleItem, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def load_oracle_manifest(path: str | Path) -> tuple[OracleInstance, ...]:
    """Load a path-contained export of resolved SWE-bench-style instances."""
    manifest = Path(path).resolve()
    payload = json.loads(manifest.read_text())
    if payload.get("schema_version") != MANIFEST_VERSION or not isinstance(
        payload.get("instances"), list
    ):
        raise ValueError("invalid oracle-gap manifest")
    root = manifest.parent
    seen: set[str] = set()
    instances: list[OracleInstance] = []
    for raw in payload["instances"]:
        if not isinstance(raw, dict):
            raise ValueError("oracle-gap instance must be an object")
        instance_id = str(raw.get("instance_id", ""))
        if not _ID.fullmatch(instance_id) or instance_id in seen:
            raise ValueError(f"invalid or duplicate instance id: {instance_id!r}")
        seen.add(instance_id)
        resolved = _contained(root, raw.get("resolved"), directory=True)
        test = _contained(resolved, raw.get("test_path"), directory=False)
        test_path = test.relative_to(resolved).as_posix()
        source_paths = tuple(
            _safe_relative(value, suffix=".py") for value in raw.get("source_paths", [])
        )
        if not source_paths:
            raise ValueError(f"instance {instance_id!r} has no mutation source paths")
        raw_lines = raw.get("suspect_lines")
        suspect_lines = None
        if raw_lines is not None:
            if not isinstance(raw_lines, dict):
                raise ValueError("suspect_lines must be an object")
            suspect_lines = {
                _safe_relative(key, suffix=".py"): {int(line) for line in lines}
                for key, lines in raw_lines.items()
            }
        command = str(raw.get("command") or f"{sys.executable} -m pytest -x -q {test_path}")
        instances.append(
            OracleInstance(
                instance_id=instance_id,
                resolved=RepoState(str(resolved), "resolved", source=f"oracle:{instance_id}"),
                test_path=test_path,
                test_code=test.read_text(),
                command=command,
                source_paths=source_paths,
                suspect_lines=suspect_lines,
            )
        )
    return tuple(instances)


def run_oracle_gap(
    manifest: str | Path,
    executor: Executor,
    *,
    reruns: int = 2,
    max_mutants: int = 128,
    timeout_s: int = 120,
) -> OracleGapReport:
    """Measure mutants surviving official fail-to-pass tests on resolved states."""
    instances = load_oracle_manifest(manifest)
    items: list[OracleItem] = []
    try:
        for instance in instances:
            image = executor.prepare(instance.resolved)
            mutations = discover_mutations(
                instance.resolved.path,
                instance.source_paths,
                suspect_lines=instance.suspect_lines,
                limit=max_mutants,
            )
            score = score_mutations(
                executor,
                instance.resolved,
                ExecSpec(
                    test_path=instance.test_path,
                    test_code=instance.test_code,
                    command=instance.command,
                    timeout_s=timeout_s,
                    image=image,
                ),
                mutations,
                reruns=reruns,
            )
            items.append(_item(instance.instance_id, score))
    finally:
        executor.close()
    eligible = sum(item.eligible for item in items)
    killed = sum(item.killed for item in items)
    survived = sum(item.survived for item in items)
    return OracleGapReport(
        schema_version=SCHEMA_VERSION,
        engine_version=ENGINE_VERSION,
        id=uuid.uuid4().hex[:12],
        created_at=datetime.now(timezone.utc).isoformat(),
        corpus=str(Path(manifest).resolve()),
        instances=len(items),
        evaluated_instances=sum(item.baseline_passed for item in items),
        generated=sum(item.generated for item in items),
        eligible=eligible,
        killed=killed,
        survived=survived,
        invalid=sum(item.invalid for item in items),
        kill_rate=killed / eligible if eligible else None,
        oracle_gap=survived / eligible if eligible else None,
        items=tuple(items),
    )


def save_oracle_gap_report(report: OracleGapReport, root: str | Path) -> Path:
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{report.id}.json"
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return path


def _item(instance_id: str, score: MutationScore) -> OracleItem:
    return OracleItem(
        instance_id=instance_id,
        baseline_passed=score.baseline_passed,
        generated=score.generated,
        eligible=score.eligible,
        killed=score.killed,
        survived=score.survived,
        invalid=score.invalid,
        kill_rate=score.kill_rate,
        oracle_gap=score.survived / score.eligible if score.eligible else None,
        reason=score.reason,
        survivors=tuple(
            result.mutation.id for result in score.results if result.status.value == "survived"
        ),
    )


def _safe_relative(value: object, *, suffix: str | None = None) -> str:
    if not isinstance(value, str):
        raise ValueError("manifest path must be a string")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or (suffix and path.suffix != suffix):
        raise ValueError(f"unsafe manifest path: {value!r}")
    return path.as_posix()


def _contained(root: Path, value: object, *, directory: bool) -> Path:
    relative = _safe_relative(value)
    try:
        resolved = root.joinpath(*PurePosixPath(relative).parts).resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"manifest path does not exist: {value!r}") from exc
    if not resolved.is_relative_to(root) or (resolved.is_dir() != directory):
        raise ValueError(f"manifest path escapes corpus: {value!r}")
    return resolved
