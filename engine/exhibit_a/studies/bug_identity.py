"""Execution-based semantic bug identity over sealed PROVEN Cases."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath

from ..engine import candidate_policy_reason
from ..executor.base import ExecSpec, Executor, RepoState
from ..hypothesis.generator import Candidate
from ..store.suite_gap import ENGINE_VERSION
from ..verdict.flip_check import detect_infra_failure, extract_signature, signatures_match

SCHEMA_VERSION = "bug-identity/v1"
MANIFEST_VERSION = "bug-identity-corpus/v1"
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class PairStatus(str, Enum):
    EQUIVALENT = "equivalent"
    DISTINCT = "distinct"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class BugArtifact:
    id: str
    target: RepoState
    fixed: RepoState
    spec: ExecSpec
    expected_signature: str | None


@dataclass(frozen=True)
class PairIdentity:
    left: str
    right: str
    status: PairStatus
    left_on_right_fix: tuple[bool, ...]
    right_on_left_fix: tuple[bool, ...]
    reason: str | None


@dataclass(frozen=True)
class BugIdentityReport:
    schema_version: str
    engine_version: str
    id: str
    created_at: str
    corpus: str
    valid_cases: tuple[str, ...]
    invalid_cases: dict[str, str]
    pairs: tuple[PairIdentity, ...]
    clusters: tuple[tuple[str, ...], ...]

    def to_dict(self) -> dict:
        return asdict(self)


def load_bug_corpus(path: str | Path) -> tuple[BugArtifact, ...]:
    manifest = Path(path).resolve()
    payload = json.loads(manifest.read_text())
    if payload.get("schema_version") != MANIFEST_VERSION or not isinstance(
        payload.get("cases"), list
    ):
        raise ValueError("invalid bug-identity manifest")
    root = manifest.parent
    artifacts: list[BugArtifact] = []
    seen: set[str] = set()
    for raw in payload["cases"]:
        if not isinstance(raw, dict):
            raise ValueError("bug-identity entry must be an object")
        case_path = _contained(root, raw.get("case"), directory=False)
        case = json.loads(case_path.read_text())
        case_id = str(case.get("id", ""))
        if not _ID.fullmatch(case_id) or case_id in seen or case.get("verdict") != "PROVEN":
            raise ValueError(f"invalid, duplicate, or non-PROVEN Case: {case_id!r}")
        seen.add(case_id)
        test = case.get("test_file")
        if not isinstance(test, dict):
            raise ValueError(f"Case {case_id!r} has no test artifact")
        candidate = Candidate(
            hypothesis="sealed evidence",
            test_path=str(test.get("path", "")),
            test_code=str(test.get("code", "")),
            run_command=str(case.get("run_command", "")),
            expected_signature=case.get("evidence", {}).get("fail_signature"),
        )
        reason = candidate_policy_reason(candidate)
        if reason:
            raise ValueError(f"Case {case_id!r} violates candidate policy: {reason}")
        target = _contained(root, raw.get("target"), directory=True)
        fixed = _contained(root, raw.get("fixed"), directory=True)
        artifacts.append(
            BugArtifact(
                case_id,
                RepoState(str(target), "target", source=f"bug-identity:{case_id}:target"),
                RepoState(str(fixed), "base", source=f"bug-identity:{case_id}:fixed"),
                ExecSpec(candidate.test_path, candidate.test_code, candidate.run_command),
                candidate.expected_signature,
            )
        )
    return tuple(artifacts)


def run_bug_identity(
    manifest: str | Path, executor: Executor, *, reruns: int = 2
) -> BugIdentityReport:
    if reruns < 1:
        raise ValueError("dedup reruns must be positive")
    artifacts = load_bug_corpus(manifest)
    valid: list[BugArtifact] = []
    invalid: dict[str, str] = {}
    try:
        for artifact in artifacts:
            reason = _validate_flip(executor, artifact, reruns)
            if reason:
                invalid[artifact.id] = reason
            else:
                valid.append(artifact)
        pairs = tuple(
            _compare(executor, left, right, reruns)
            for index, left in enumerate(valid)
            for right in valid[index + 1 :]
        )
    finally:
        executor.close()
    return BugIdentityReport(
        schema_version=SCHEMA_VERSION,
        engine_version=ENGINE_VERSION,
        id=uuid.uuid4().hex[:12],
        created_at=datetime.now(timezone.utc).isoformat(),
        corpus=str(Path(manifest).resolve()),
        valid_cases=tuple(item.id for item in valid),
        invalid_cases=invalid,
        pairs=pairs,
        clusters=_complete_link_clusters(valid, pairs),
    )


def save_bug_identity_report(report: BugIdentityReport, root: str | Path) -> Path:
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{report.id}.json"
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return path


def _validate_flip(executor: Executor, artifact: BugArtifact, reruns: int) -> str | None:
    target = [executor.run(artifact.target, artifact.spec) for _ in range(reruns)]
    fixed = [executor.run(artifact.fixed, artifact.spec) for _ in range(reruns)]
    if not all(outcome.passed for outcome in fixed):
        return "sealed test no longer passes deterministically on its own fix"
    if any(outcome.passed for outcome in target):
        return "sealed test no longer fails deterministically on its own target"
    if not all(
        signatures_match(artifact.expected_signature, extract_signature(outcome))
        for outcome in target
    ):
        return "sealed target failure no longer matches its admitted signature"
    return None


def _compare(
    executor: Executor, left: BugArtifact, right: BugArtifact, reruns: int
) -> PairIdentity:
    left_runs = tuple(executor.run(right.fixed, left.spec) for _ in range(reruns))
    right_runs = tuple(executor.run(left.fixed, right.spec) for _ in range(reruns))
    all_runs = left_runs + right_runs
    infra = next((detect_infra_failure(run) for run in all_runs if detect_infra_failure(run)), None)
    outcomes = tuple(run.passed for run in left_runs), tuple(run.passed for run in right_runs)
    if infra:
        status, reason = PairStatus.INCONCLUSIVE, infra
    elif any(outcomes[0]) != all(outcomes[0]) or any(outcomes[1]) != all(outcomes[1]):
        status, reason = PairStatus.INCONCLUSIVE, "cross-fix result was nondeterministic"
    elif all(outcomes[0]) and all(outcomes[1]):
        status, reason = PairStatus.EQUIVALENT, None
    else:
        status, reason = PairStatus.DISTINCT, "at least one test still fails on the other fix"
    return PairIdentity(left.id, right.id, status, outcomes[0], outcomes[1], reason)


def _complete_link_clusters(
    artifacts: list[BugArtifact], pairs: tuple[PairIdentity, ...]
) -> tuple[tuple[str, ...], ...]:
    equivalent = {
        frozenset((pair.left, pair.right)) for pair in pairs if pair.status is PairStatus.EQUIVALENT
    }
    clusters: list[list[str]] = []
    for artifact in artifacts:
        destination = next(
            (
                cluster
                for cluster in clusters
                if all(frozenset((artifact.id, member)) in equivalent for member in cluster)
            ),
            None,
        )
        if destination is None:
            clusters.append([artifact.id])
        else:
            destination.append(artifact.id)
    return tuple(tuple(cluster) for cluster in clusters)


def _safe_relative(value: object) -> PurePosixPath:
    if not isinstance(value, str):
        raise ValueError("manifest path must be a string")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe manifest path: {value!r}")
    return path


def _contained(root: Path, value: object, *, directory: bool) -> Path:
    relative = _safe_relative(value)
    try:
        resolved = root.joinpath(*relative.parts).resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"manifest path does not exist: {value!r}") from exc
    if not resolved.is_relative_to(root) or (resolved.is_dir() != directory):
        raise ValueError(f"manifest path escapes corpus: {value!r}")
    return resolved
