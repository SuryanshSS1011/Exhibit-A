"""Cross-version execution timeline for one frozen, admitted reproduction."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from ..engine import candidate_policy_reason
from ..executor.base import ExecSpec, Executor
from ..hypothesis.generator import Candidate
from ..intake.git_checkout import checkout_context, validate_repo_url, validate_sha
from ..store.suite_gap import ENGINE_VERSION
from ..verdict.flip_check import detect_infra_failure, extract_signature, signatures_match

SCHEMA_VERSION = "evidence-archaeology/v1"


class RevisionStatus(str, Enum):
    PASS = "pass"
    FAIL_MATCH = "fail_match"
    FAIL_OTHER = "fail_other"
    FLAKY = "flaky"
    INFRA = "infra"


class Attribution(str, Enum):
    PRE_EXISTING = "pre_existing_before_window"
    INTRODUCED = "introduced_in_window"
    NEVER_OBSERVED = "never_observed"
    NON_MONOTONIC = "non_monotonic"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class RevisionObservation:
    sha: str
    status: RevisionStatus
    passes: tuple[bool, ...]
    signatures: tuple[str | None, ...]
    reason: str | None


@dataclass(frozen=True)
class ArchaeologyReport:
    schema_version: str
    engine_version: str
    id: str
    created_at: str
    case_id: str
    repo_url: str
    order: str
    attribution: Attribution
    boundary: tuple[str, str] | None
    observations: tuple[RevisionObservation, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def run_archaeology(
    case_path: str | Path,
    repo_url: str,
    shas: list[str],
    executor: Executor,
    *,
    reruns: int = 2,
) -> ArchaeologyReport:
    """Run a frozen test across oldest-to-newest pinned revisions."""
    validate_repo_url(repo_url)
    if len(shas) < 2 or reruns < 1:
        raise ValueError("archaeology requires at least two SHAs and positive reruns")
    for sha in shas:
        validate_sha(sha)
    if len(set(shas)) != len(shas):
        raise ValueError("archaeology SHAs must be unique")
    case_id, spec, expected = _load_case(case_path)
    observations: list[RevisionObservation] = []
    try:
        for sha in shas:
            with checkout_context(repo_url, sha, label="historical") as state:
                image = executor.prepare(state)
                revision_spec = ExecSpec(
                    spec.test_path,
                    spec.test_code,
                    spec.command,
                    timeout_s=spec.timeout_s,
                    image=image,
                )
                outcomes = tuple(executor.run(state, revision_spec) for _ in range(reruns))
                observations.append(_classify(sha, outcomes, expected))
    finally:
        executor.close()
    attribution, boundary = _attribute(observations)
    return ArchaeologyReport(
        schema_version=SCHEMA_VERSION,
        engine_version=ENGINE_VERSION,
        id=uuid.uuid4().hex[:12],
        created_at=datetime.now(timezone.utc).isoformat(),
        case_id=case_id,
        repo_url=repo_url,
        order="oldest_to_newest",
        attribution=attribution,
        boundary=boundary,
        observations=tuple(observations),
    )


def save_archaeology_report(report: ArchaeologyReport, root: str | Path) -> Path:
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{report.id}.json"
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return path


def _load_case(path: str | Path) -> tuple[str, ExecSpec, str | None]:
    case = json.loads(Path(path).read_text())
    if case.get("verdict") != "PROVEN" or not isinstance(case.get("test_file"), dict):
        raise ValueError("archaeology requires a sealed PROVEN Case")
    test = case["test_file"]
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
        str(case.get("id", "unknown")),
        ExecSpec(candidate.test_path, candidate.test_code, candidate.run_command),
        candidate.expected_signature,
    )


def _classify(sha, outcomes, expected) -> RevisionObservation:
    passes = tuple(outcome.passed for outcome in outcomes)
    signatures = tuple(extract_signature(outcome) for outcome in outcomes)
    infra = next((detect_infra_failure(run) for run in outcomes if detect_infra_failure(run)), None)
    if infra:
        return RevisionObservation(sha, RevisionStatus.INFRA, passes, signatures, infra)
    if any(passes) and not all(passes):
        return RevisionObservation(
            sha, RevisionStatus.FLAKY, passes, signatures, "revision was nondeterministic"
        )
    if all(passes):
        return RevisionObservation(sha, RevisionStatus.PASS, passes, signatures, None)
    if all(signatures_match(expected, signature) for signature in signatures):
        return RevisionObservation(sha, RevisionStatus.FAIL_MATCH, passes, signatures, None)
    return RevisionObservation(
        sha, RevisionStatus.FAIL_OTHER, passes, signatures, "failure signature differs"
    )


def _attribute(
    observations: list[RevisionObservation],
) -> tuple[Attribution, tuple[str, str] | None]:
    statuses = [item.status for item in observations]
    if any(status in {RevisionStatus.INFRA, RevisionStatus.FLAKY} for status in statuses):
        return Attribution.INCONCLUSIVE, None
    failing = [status is RevisionStatus.FAIL_MATCH for status in statuses]
    if all(failing):
        return Attribution.PRE_EXISTING, None
    if not any(failing):
        return Attribution.NEVER_OBSERVED, None
    transitions = [
        index for index in range(1, len(failing)) if failing[index] != failing[index - 1]
    ]
    if len(transitions) == 1 and not failing[0] and failing[-1]:
        index = transitions[0]
        return Attribution.INTRODUCED, (observations[index - 1].sha, observations[index].sha)
    return Attribution.NON_MONOTONIC, None
