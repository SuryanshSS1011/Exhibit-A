"""Execute an untrusted minimal counterpatch against frozen evidence and the full suite."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath

from ..engine import candidate_policy_reason
from ..executor.base import ExecSpec, Executor, RepoState
from ..hypothesis.counterpatch import CounterpatchGenerator
from ..hypothesis.generator import Candidate
from ..store.suite_gap import ENGINE_VERSION
from ..verdict.flip_check import extract_signature, signatures_match

SCHEMA_VERSION = "counterpatch-triangulation/v1"
_DIFF_PATH = re.compile(r"^diff --git a/(\S+) b/(\S+)$", re.MULTILINE)


class TriangulationStatus(str, Enum):
    VIABLE = "viable_counterpatch"
    REJECTED = "rejected_counterpatch"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class TriangulationReport:
    schema_version: str
    engine_version: str
    id: str
    created_at: str
    case_id: str
    status: TriangulationStatus
    rationale: str | None
    patch: str | None
    touched_files: tuple[str, ...]
    changed_lines: int
    target_test_passes: tuple[bool, ...]
    patched_test_passes: tuple[bool, ...]
    baseline_suite_passed: bool | None
    patched_suite_passed: bool | None
    reason: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def run_triangulation(
    case_path: str | Path,
    target_path: str | Path,
    allowed_sources: list[str],
    generator: CounterpatchGenerator,
    executor: Executor,
    *,
    suite_argv: list[str] | None = None,
    reruns: int = 2,
) -> TriangulationReport:
    if reruns < 1:
        raise ValueError("triangulation reruns must be positive")
    case, spec, expected = _load_case(case_path)
    target = Path(target_path).resolve()
    if not target.is_dir():
        raise ValueError(f"target checkout not found: {target}")
    sources = tuple(_safe_source(path) for path in allowed_sources)
    if not sources:
        raise ValueError("triangulation requires at least one allowed source path")
    proposal = generator.propose(case, str(target), sources)
    if proposal is None:
        return _empty(case, "generator declined to propose a counterpatch")
    touched, changed = validate_counterpatch(proposal.patch, sources, spec.test_path)
    workdir = Path(tempfile.mkdtemp(prefix="exhibit-a-counterpatch-"))
    try:
        patched = workdir / "repo"
        shutil.copytree(target, patched, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        _apply_patch(patched, proposal.patch)
        target_state = RepoState(str(target), "target", source=str(target))
        patched_state = RepoState(str(patched), "counterpatch", source=str(target))
        target_image = executor.prepare(target_state)
        patched_image = executor.prepare(patched_state)
        target_spec = _with_image(spec, target_image)
        patched_spec = _with_image(spec, patched_image)
        target_runs = tuple(executor.run(target_state, target_spec) for _ in range(reruns))
        patched_runs = tuple(executor.run(patched_state, patched_spec) for _ in range(reruns))
        target_valid = all(
            not run.passed and signatures_match(expected, extract_signature(run))
            for run in target_runs
        )
        patched_valid = all(run.passed for run in patched_runs)
        suite = suite_argv or ["python3", "-m", "pytest", "-q"]
        baseline_suite = executor.run_suite(target_state, suite, image=target_image)
        patched_suite = executor.run_suite(patched_state, suite, image=patched_image)
        if not target_valid:
            status, reason = (
                TriangulationStatus.INCONCLUSIVE,
                "sealed target failure did not revalidate",
            )
        elif not patched_valid:
            status, reason = (
                TriangulationStatus.REJECTED,
                "counterpatch did not pass the frozen test",
            )
        elif baseline_suite is None or patched_suite is None:
            status, reason = TriangulationStatus.INCONCLUSIVE, "executor cannot run the full suite"
        elif not baseline_suite.passed:
            status, reason = TriangulationStatus.INCONCLUSIVE, "baseline full suite was not green"
        elif not patched_suite.passed:
            status, reason = TriangulationStatus.REJECTED, "counterpatch regressed the full suite"
        else:
            status, reason = TriangulationStatus.VIABLE, None
        return TriangulationReport(
            SCHEMA_VERSION,
            ENGINE_VERSION,
            uuid.uuid4().hex[:12],
            datetime.now(timezone.utc).isoformat(),
            str(case.get("id", "unknown")),
            status,
            proposal.rationale,
            proposal.patch,
            touched,
            changed,
            tuple(run.passed for run in target_runs),
            tuple(run.passed for run in patched_runs),
            baseline_suite.passed if baseline_suite else None,
            patched_suite.passed if patched_suite else None,
            reason,
        )
    finally:
        executor.close()
        shutil.rmtree(workdir, ignore_errors=True)


def save_triangulation_report(report: TriangulationReport, root: str | Path) -> Path:
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{report.id}.json"
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return path


def validate_counterpatch(
    patch: str, allowed_sources: tuple[str, ...], test_path: str
) -> tuple[tuple[str, ...], int]:
    if not patch or len(patch.encode()) > 64_000 or "GIT binary patch" in patch:
        raise ValueError("counterpatch is empty, oversized, or binary")
    matches = _DIFF_PATH.findall(patch)
    if not matches or any(left != right for left, right in matches):
        raise ValueError("counterpatch must modify existing files with canonical diff paths")
    touched = tuple(dict.fromkeys(left for left, _ in matches))
    if any(path not in allowed_sources or path == test_path for path in touched):
        raise ValueError("counterpatch touches a path outside the allowed production scope")
    for line in patch.splitlines():
        if line.startswith(("--- ", "+++ ")) and "/dev/null" in line:
            raise ValueError("counterpatch cannot add or delete files")
    changed = sum(
        1
        for line in patch.splitlines()
        if (line.startswith("+") or line.startswith("-")) and not line.startswith(("+++", "---"))
    )
    if changed > 80:
        raise ValueError("counterpatch exceeds the 80-line minimality budget")
    return touched, changed


def _apply_patch(root: Path, patch: str) -> None:
    argv = [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-C",
        str(root),
        "apply",
        "--no-index",
        "--whitespace=error-all",
        "-",
    ]
    proc = subprocess.run(argv, input=patch, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ValueError(
            f"counterpatch did not apply cleanly: {(proc.stderr or proc.stdout)[-1000:]}"
        )


def _load_case(path: str | Path) -> tuple[dict, ExecSpec, str | None]:
    case = json.loads(Path(path).read_text())
    test = case.get("test_file")
    if case.get("verdict") != "PROVEN" or not isinstance(test, dict):
        raise ValueError("triangulation requires a sealed PROVEN Case")
    candidate = Candidate(
        "frozen evidence",
        str(test.get("path", "")),
        str(test.get("code", "")),
        str(case.get("run_command", "")),
        case.get("evidence", {}).get("fail_signature"),
    )
    reason = candidate_policy_reason(candidate)
    if reason:
        raise ValueError(f"Case violates candidate policy: {reason}")
    return (
        case,
        ExecSpec(candidate.test_path, candidate.test_code, candidate.run_command),
        candidate.expected_signature,
    )


def _safe_source(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or path.suffix != ".py":
        raise ValueError(f"unsafe counterpatch source path: {value!r}")
    return path.as_posix()


def _with_image(spec: ExecSpec, image: str | None) -> ExecSpec:
    return ExecSpec(spec.test_path, spec.test_code, spec.command, spec.timeout_s, image=image)


def _empty(case: dict, reason: str) -> TriangulationReport:
    return TriangulationReport(
        SCHEMA_VERSION,
        ENGINE_VERSION,
        uuid.uuid4().hex[:12],
        datetime.now(timezone.utc).isoformat(),
        str(case.get("id", "unknown")),
        TriangulationStatus.INCONCLUSIVE,
        None,
        None,
        (),
        0,
        (),
        (),
        None,
        None,
        reason,
    )
