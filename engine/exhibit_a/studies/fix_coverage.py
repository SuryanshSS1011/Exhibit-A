"""Corpus-level measurement of how often real fixes reach Exhibit A evidence tiers.

This module observes ordinary Evidence Engine runs. It has no verdict authority and
must never be imported by the deterministic verdict package.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from ..store.suite_gap import ENGINE_VERSION

CORPUS_SCHEMA = "fix-coverage-corpus/v1"
REPORT_SCHEMA = "fix-coverage-study/v1"
CHECKPOINT_SCHEMA = "fix-coverage-instance/v1"
WORKER_SCHEMA = "fix-coverage-worker/v1"
RUN_STATE_SCHEMA = "fix-coverage-run-state/v1"
TAXONOMY_SCHEMA = "fix-coverage-failure-taxonomy/v2"

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_CATCH_ALL = frozenset({"candidate_other_rejection", "study_error"})
_SERVICE_PATTERNS = (
    re.compile(r"connection (?:was )?refused"),
    re.compile(r"could not connect to (?:database|server)"),
    re.compile(r"(?:service|server) unavailable"),
    re.compile(r"failed to connect to"),
    re.compile(
        r"(?:redis|postgres(?:ql)?|mysql|mongodb|kafka|rabbitmq|elastic ?search)"
        r".{0,100}(?:connection|server|service|unavailable|refused|not running|timed? out)"
    ),
    re.compile(
        r"(?:connection|server|service|unavailable|refused|not running|timed? out)"
        r".{0,100}(?:redis|postgres(?:ql)?|mysql|mongodb|kafka|rabbitmq|elastic ?search)"
    ),
)


@dataclass(frozen=True)
class FixInstance:
    id: str
    repository: str
    buggy_sha: str
    fix_sha: str
    claim: str
    commit_date: str
    source_url: str | None = None


@dataclass(frozen=True)
class FixCorpus:
    path: str
    sha256: str
    preregistration: dict
    selection: dict
    exclusions: tuple[dict, ...]
    instances: tuple[FixInstance, ...]


@dataclass(frozen=True)
class RunConfig:
    requested_model: str
    provider_config: str | None
    instance_timeout_s: float
    total_ceiling_s: float
    execution_timeout_s: int
    reruns: int
    max_refine: int
    check_existing_suite: bool = True
    minimize_verified: bool = False
    score_evidence_strength: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class WorkerResult:
    instance_id: str
    started_at: str
    finished_at: str
    wall_time_s: float
    case: dict | None
    error_type: str | None
    error: str | None
    timed_out: bool = False
    exit_code: int | None = None
    attempt: int = 1
    prior_incomplete_attempts: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


TrialRunner = Callable[[FixInstance, Path, float], WorkerResult]


def load_fix_corpus(path: str | Path) -> FixCorpus:
    """Load a stable, public manifest without resolving or cloning its URLs."""
    manifest_path = Path(path).resolve(strict=True)
    raw = manifest_path.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("schema_version") != CORPUS_SCHEMA:
        raise ValueError(f"corpus schema must be {CORPUS_SCHEMA!r}")
    required = {"preregistration", "selection", "exclusions", "instances"}
    if not required.issubset(payload):
        raise ValueError(f"corpus manifest is missing fields: {sorted(required - set(payload))}")
    if not isinstance(payload["preregistration"], dict):
        raise TypeError("corpus preregistration reference must be an object")
    if not isinstance(payload["selection"], dict):
        raise TypeError("corpus selection record must be an object")
    if not isinstance(payload["exclusions"], list):
        raise TypeError("corpus exclusions must be an array")
    if not isinstance(payload["instances"], list) or not payload["instances"]:
        raise ValueError("corpus requires at least one instance")

    instances: list[FixInstance] = []
    seen_ids: set[str] = set()
    seen_pairs: set[tuple[str, str, str]] = set()
    for item in payload["instances"]:
        if not isinstance(item, dict):
            raise TypeError("corpus instance must be an object")
        instance = _parse_instance(item)
        pair = (instance.repository, instance.buggy_sha, instance.fix_sha)
        if instance.id in seen_ids or pair in seen_pairs:
            raise ValueError(f"duplicate corpus instance: {instance.id!r}")
        seen_ids.add(instance.id)
        seen_pairs.add(pair)
        instances.append(instance)

    exclusions = []
    for item in payload["exclusions"]:
        if not isinstance(item, dict):
            raise TypeError("corpus exclusion must be an object")
        reason = item.get("reason_code")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("corpus exclusion requires reason_code")
        exclusions.append(dict(item))
    return FixCorpus(
        path=str(manifest_path),
        sha256=hashlib.sha256(raw).hexdigest(),
        preregistration=dict(payload["preregistration"]),
        selection=dict(payload["selection"]),
        exclusions=tuple(exclusions),
        instances=tuple(instances),
    )


def run_fix_coverage_study(
    *,
    corpus: FixCorpus,
    output_root: str | Path,
    config: RunConfig,
    runner: TrialRunner,
) -> dict:
    """Run each fixed instance at most once per completed attempt and checkpoint it."""
    _validate_config(config)
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    state = _load_or_create_state(root, corpus, config)
    checkpoints = root / "checkpoints"
    checkpoints.mkdir(exist_ok=True)

    records = _load_checkpoints(checkpoints, corpus, state["config_sha256"])
    active_s = sum(float(record["result"]["wall_time_s"]) for record in records.values())
    for instance in corpus.instances:
        if instance.id in records:
            continue
        remaining_s = config.total_ceiling_s - active_s
        if remaining_s <= 0:
            break
        result = runner(instance, root, min(config.instance_timeout_s, remaining_s))
        category, reason = classify_worker_result(result)
        checkpoint = {
            "schema_version": CHECKPOINT_SCHEMA,
            "engine_version": ENGINE_VERSION,
            "manifest_sha256": corpus.sha256,
            "config_sha256": state["config_sha256"],
            "instance": asdict(instance),
            "result": result.to_dict(),
            "failure_category": category,
            "failure_reason": reason,
        }
        _atomic_json(checkpoints / f"{instance.id}.json", checkpoint)
        records[instance.id] = checkpoint
        active_s += result.wall_time_s
        _atomic_json(root / "report.json", _aggregate(corpus, config, state, records))

    report = _aggregate(corpus, config, state, records)
    _atomic_json(root / "report.json", report)
    return report


def classify_worker_result(result: WorkerResult) -> tuple[str | None, str | None]:
    """Map one observed run into a stable, non-overlapping failure taxonomy."""
    if result.timed_out:
        return "timed_out", result.error or "per-instance wall-clock limit exceeded"
    if result.case is None:
        detail = result.error or "worker produced no Case"
        lowered = detail.lower()
        if any(marker in lowered for marker in ("clone", "checkout", "fetch", "repo url")):
            return "checkout_failed", detail
        return "study_error", detail

    case = result.case
    verdict = str(case.get("verdict", "UNCERTAIN"))
    if verdict in {"VERIFIED", "PARTIAL"}:
        return None, None
    silence = str(case.get("silence_reason") or "")
    lowered = silence.lower()
    logs = _case_logs(case).lower()
    combined = f"{lowered}\n{logs}"

    if "could not build environment" in lowered:
        if "no poetry.lock" in lowered or "no supported lock" in lowered:
            return "environment_no_supported_lockfile", silence
        if "dependency image failed to build" in lowered or "pip install" in combined:
            return "environment_dependency_install_failed", silence
        return "environment_other_setup_failed", silence
    if "timed out" in combined or "timeout:" in combined:
        return "timed_out", silence or "an execution exceeded its wall-clock limit"
    if case.get("existing_suite_passed") is False:
        if _needs_service(combined):
            return "suite_requires_unavailable_services", silence
        return "existing_suite_failed", silence
    if "existing test suite could not be evaluated safely" in lowered:
        if _needs_service(combined):
            return "suite_requires_unavailable_services", silence
        return "suite_infrastructure_failure", silence

    hypotheses = case.get("hypotheses")
    if not isinstance(hypotheses, list) or not hypotheses:
        if any(
            marker in lowered
            for marker in (
                "usage limit",
                "insufficient_quota",
                "billing hard limit",
                "purchase more credits",
            )
        ):
            return "provider_quota_exhausted", silence
        if "generation failed" in lowered or "provider" in lowered:
            return "provider_generation_failed", silence
        return "no_candidate_proposed", silence or "the provider proposed no candidate"
    reasons = "\n".join(
        str(item.get("reason") or "") for item in hypotheses if isinstance(item, dict)
    )
    reason_text = (reasons or silence).lower()
    if _needs_service(f"{reason_text}\n{logs}"):
        return "suite_requires_unavailable_services", reasons or silence
    if "flaky on target" in reason_text:
        return "candidate_flaky", reasons or silence
    if "harness-tamper" in reason_text:
        return "candidate_tamper", reasons or silence
    if any(
        marker in reason_text
        for marker in ("no assertion", "imports nothing", "imports only stdlib/pytest")
    ):
        return "candidate_vacuous", reasons or silence
    if "failed for the wrong reason" in reason_text:
        return "candidate_wrong_failure_signature", reasons or silence
    if "environmental/harness reason" in reason_text or "collection/usage error" in reason_text:
        return "candidate_infrastructure_failure", reasons or silence
    if "does not fail on the target" in reason_text:
        return "candidate_did_not_fail_on_buggy", reasons or silence
    if "does not pass on the base/fixed state" in reason_text:
        return "candidate_failed_on_fixed", reasons or silence
    if any(marker in reason_text for marker in ("test path", "run command", "outside")):
        return "candidate_policy_rejection", reasons or silence
    return "candidate_other_rejection", reasons or silence or "unclassified rejection"


class SubprocessTrialRunner:
    """Execute each instance in a killable child process with durable raw results."""

    def __init__(self, config: RunConfig):
        self.config = config

    def __call__(self, instance: FixInstance, root: Path, timeout_s: float) -> WorkerResult:
        workers = root / "workers" / instance.id
        workers.mkdir(parents=True, exist_ok=True)
        complete = sorted(workers.glob("attempt-*/result.json"))
        if complete:
            return _load_worker_result(complete[0])
        incomplete = [
            path for path in workers.glob("attempt-*") if not (path / "result.json").is_file()
        ]
        attempt = len(list(workers.glob("attempt-*"))) + 1
        attempt_root = workers / f"attempt-{attempt:03d}"
        attempt_root.mkdir()
        result_path = attempt_root / "result.json"
        request = {
            "schema_version": WORKER_SCHEMA,
            "instance": asdict(instance),
            "config": self.config.to_dict(),
            "attempt": attempt,
            "prior_incomplete_attempts": len(incomplete),
            "environment_root": str(attempt_root / "environment-attempts"),
        }
        request_path = attempt_root / "request.json"
        _atomic_json(request_path, request)
        started = datetime.now(timezone.utc).isoformat()
        start = time.monotonic()
        with (
            (attempt_root / "stdout.log").open("w") as stdout,
            (attempt_root / "stderr.log").open("w") as stderr,
        ):
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "exhibit_a.studies.fix_coverage_worker",
                    "--request",
                    str(request_path),
                    "--out",
                    str(result_path),
                ],
                stdout=stdout,
                stderr=stderr,
                text=True,
                start_new_session=True,
            )
            try:
                exit_code = process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                _terminate_process_group(process)
                return WorkerResult(
                    instance_id=instance.id,
                    started_at=started,
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    wall_time_s=round(time.monotonic() - start, 6),
                    case=None,
                    error_type="TimeoutExpired",
                    error=f"per-instance ceiling of {timeout_s:.3f}s exceeded",
                    timed_out=True,
                    exit_code=None,
                    attempt=attempt,
                    prior_incomplete_attempts=len(incomplete),
                )
        if result_path.is_file():
            return _load_worker_result(result_path)
        error = _tail(attempt_root / "stderr.log") or _tail(attempt_root / "stdout.log")
        return WorkerResult(
            instance_id=instance.id,
            started_at=started,
            finished_at=datetime.now(timezone.utc).isoformat(),
            wall_time_s=round(time.monotonic() - start, 6),
            case=None,
            error_type="WorkerExit",
            error=error or f"worker exited {exit_code} without a result",
            exit_code=exit_code,
            attempt=attempt,
            prior_incomplete_attempts=len(incomplete),
        )


def _parse_instance(item: dict) -> FixInstance:
    identifier = item.get("id")
    if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
        raise ValueError(f"invalid corpus instance id: {identifier!r}")
    repository = item.get("repository")
    if not isinstance(repository, str):
        raise TypeError(f"repository for {identifier!r} must be a string")
    parsed = urlsplit(repository)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"repository for {identifier!r} must be credential-free HTTPS")
    buggy_sha = item.get("buggy_sha")
    fix_sha = item.get("fix_sha")
    if not isinstance(buggy_sha, str) or not _SHA.fullmatch(buggy_sha):
        raise ValueError(f"buggy_sha for {identifier!r} must be a full lowercase SHA-1")
    if not isinstance(fix_sha, str) or not _SHA.fullmatch(fix_sha) or fix_sha == buggy_sha:
        raise ValueError(f"fix_sha for {identifier!r} must be a distinct full lowercase SHA-1")
    claim = item.get("claim")
    if not isinstance(claim, str) or not claim.strip() or len(claim) > 20_000:
        raise ValueError(f"claim for {identifier!r} must contain 1 to 20,000 characters")
    commit_date = item.get("commit_date")
    if not isinstance(commit_date, str):
        raise TypeError(f"commit_date for {identifier!r} must be a string")
    _parse_timestamp(commit_date)
    source_url = item.get("source_url")
    if source_url is not None and not isinstance(source_url, str):
        raise TypeError(f"source_url for {identifier!r} must be a string or null")
    return FixInstance(
        identifier,
        repository,
        buggy_sha,
        fix_sha,
        claim.strip(),
        commit_date,
        source_url,
    )


def _validate_config(config: RunConfig) -> None:
    if not config.requested_model.strip():
        raise ValueError("requested model must not be empty")
    if not 1 <= config.execution_timeout_s <= 600:
        raise ValueError("execution timeout must be between 1 and 600 seconds")
    if not 1 <= config.reruns <= 20:
        raise ValueError("reruns must be between 1 and 20")
    if not 0 <= config.max_refine <= 10:
        raise ValueError("max_refine must be between 0 and 10")
    for value, label in (
        (config.instance_timeout_s, "instance timeout"),
        (config.total_ceiling_s, "total ceiling"),
    ):
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{label} must be a positive finite number")
    if config.total_ceiling_s < config.instance_timeout_s:
        raise ValueError("total ceiling must be at least the per-instance timeout")


def _load_or_create_state(root: Path, corpus: FixCorpus, config: RunConfig) -> dict:
    path = root / "state.json"
    config_bytes = json.dumps(config.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    config_sha = hashlib.sha256(config_bytes).hexdigest()
    if path.exists():
        state = json.loads(path.read_text())
        if state.get("schema_version") != RUN_STATE_SCHEMA:
            raise ValueError("coverage output contains an incompatible state file")
        if state.get("manifest_sha256") != corpus.sha256:
            raise ValueError("coverage output belongs to a different corpus manifest")
        if state.get("config_sha256") != config_sha:
            raise ValueError("coverage output belongs to different run parameters")
        return state
    state = {
        "schema_version": RUN_STATE_SCHEMA,
        "engine_version": ENGINE_VERSION,
        "id": uuid.uuid4().hex[:12],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": corpus.sha256,
        "config_sha256": config_sha,
        "config": config.to_dict(),
    }
    _atomic_json(path, state)
    return state


def _load_checkpoints(directory: Path, corpus: FixCorpus, config_sha: str) -> dict[str, dict]:
    allowed = {instance.id for instance in corpus.instances}
    records = {}
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text())
        identifier = payload.get("instance", {}).get("id")
        if (
            payload.get("schema_version") != CHECKPOINT_SCHEMA
            or payload.get("manifest_sha256") != corpus.sha256
            or payload.get("config_sha256") != config_sha
            or identifier not in allowed
            or identifier in records
        ):
            raise ValueError(f"invalid coverage checkpoint: {path}")
        records[str(identifier)] = payload
    return records


def _aggregate(corpus: FixCorpus, config: RunConfig, state: dict, records: dict[str, dict]) -> dict:
    ordered = [records[item.id] for item in corpus.instances if item.id in records]
    verdicts = Counter(
        str(item["result"].get("case", {}).get("verdict", "ERROR"))
        if item["result"].get("case") is not None
        else "ERROR"
        for item in ordered
    )
    failures = Counter(
        str(item["failure_category"])
        for item in ordered
        if item.get("failure_category") is not None
    )
    requested = len(corpus.instances)
    completed = len(ordered)
    verified = verdicts["VERIFIED"]
    partial = verdicts["PARTIAL"]
    exclusion_counts = Counter(str(item["reason_code"]) for item in corpus.exclusions)
    sampling_exclusions = sum(
        exclusion_counts[reason] for reason in ("per_repository_cap", "sample_size_reached")
    )
    eligibility_exclusions = len(corpus.exclusions) - sampling_exclusions
    provider_runs = [
        proposal
        for item in ordered
        for proposal in (item["result"].get("case") or {}).get("proposal_runs", [])
        if isinstance(proposal, dict)
    ]
    identities = Counter(
        (
            str(item.get("provider")),
            str(item.get("requested_model")),
            str(item.get("confirmed_model")),
            str(item.get("confirmed_version")),
        )
        for item in provider_runs
    )
    known_costs = [
        float(item["cost_usd"]) for item in provider_runs if item.get("cost_usd") is not None
    ]
    token_fields = {
        key: [int(item[key]) for item in provider_runs if item.get(key) is not None]
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }
    catch_all = sum(failures[name] for name in _CATCH_ALL)
    failure_total = sum(failures.values())
    interval = _wilson(verified, requested)
    items = []
    for checkpoint in ordered:
        result = checkpoint["result"]
        case = result.get("case")
        items.append(
            {
                "id": checkpoint["instance"]["id"],
                "repository": checkpoint["instance"]["repository"],
                "buggy_sha": checkpoint["instance"]["buggy_sha"],
                "fix_sha": checkpoint["instance"]["fix_sha"],
                "commit_date": checkpoint["instance"]["commit_date"],
                "verdict": case.get("verdict") if isinstance(case, dict) else None,
                "failure_category": checkpoint.get("failure_category"),
                "failure_reason": checkpoint.get("failure_reason"),
                "wall_time_s": result["wall_time_s"],
                "timed_out": result["timed_out"],
                "attempt": result["attempt"],
                "prior_incomplete_attempts": result["prior_incomplete_attempts"],
                "error_type": result.get("error_type"),
            }
        )
    return {
        "schema_version": REPORT_SCHEMA,
        "failure_taxonomy_schema": TAXONOMY_SCHEMA,
        "engine_version": ENGINE_VERSION,
        "id": state["id"],
        "started_at": state["started_at"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_path": corpus.path,
        "manifest_sha256": corpus.sha256,
        "preregistration": corpus.preregistration,
        "config": config.to_dict(),
        "requested_instances": requested,
        "completed_instances": completed,
        "remaining_instances": requested - completed,
        "active_wall_time_s": round(
            sum(float(item["result"]["wall_time_s"]) for item in ordered), 6
        ),
        "headline": {
            "denominator": requested,
            "verified": verified,
            "verified_fraction": verified / requested,
            "verified_wilson_95": list(interval),
            "partial": partial,
            "partial_fraction": partial / requested,
            "attempted_denominator": completed,
            "verified_fraction_of_attempted": verified / completed if completed else None,
        },
        "verdict_counts": dict(sorted(verdicts.items())),
        "failure_taxonomy": [
            {
                "category": category,
                "count": count,
                "fraction_of_failures": count / failure_total if failure_total else None,
            }
            for category, count in sorted(failures.items(), key=lambda item: (-item[1], item[0]))
        ],
        "taxonomy_catch_all_fraction": catch_all / failure_total if failure_total else 0.0,
        "taxonomy_warning": (
            "catch-all categories exceed 20% of failures; refine before interpreting constraints"
            if failure_total and catch_all / failure_total > 0.2
            else None
        ),
        "selection": {
            **corpus.selection,
            "included_instances": requested,
            "excluded_candidates": len(corpus.exclusions),
            "exclusion_counts": dict(sorted(exclusion_counts.items())),
            "eligibility_exclusions": eligibility_exclusions,
            "unselected_eligible_candidates": sampling_exclusions,
            "screened_candidates": requested + eligibility_exclusions,
            "end_to_end_verified_fraction": (
                verified / (requested + eligibility_exclusions)
                if requested + eligibility_exclusions
                else None
            ),
        },
        "model_telemetry": {
            "calls": len(provider_runs),
            "identities": [
                {
                    "provider": key[0],
                    "requested_model": key[1],
                    "confirmed_model": key[2],
                    "confirmed_version": key[3],
                    "calls": count,
                }
                for key, count in sorted(identities.items())
            ],
            **{
                key: {
                    "reported_calls": len(values),
                    "total": sum(values) if values else None,
                }
                for key, values in token_fields.items()
            },
            "reported_cost_calls": len(known_costs),
            "reported_cost_usd": round(sum(known_costs), 8) if known_costs else None,
            "actual_model_spend_usd": (
                round(sum(known_costs), 8)
                if provider_runs and len(known_costs) == len(provider_runs)
                else None
            ),
            "spend_basis": (
                "provider-reported cost for every model call"
                if provider_runs and len(known_costs) == len(provider_runs)
                else "unavailable: at least one model call did not report billable cost"
            ),
        },
        "items": items,
    }


def _case_logs(case: dict) -> str:
    parts = [str(case.get("existing_suite_log") or "")]
    evidence = case.get("evidence")
    if isinstance(evidence, dict):
        parts.extend(str(evidence.get(key) or "") for key in ("fail_log", "pass_log"))
        runs = evidence.get("runs")
        if isinstance(runs, list):
            parts.extend(str(item.get("log") or "") for item in runs if isinstance(item, dict))
    return "\n".join(parts)


def _needs_service(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SERVICE_PATTERNS)


def _wilson(successes: int, total: int) -> tuple[float, float]:
    if total < 1:
        return (0.0, 0.0)
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return (round(max(0.0, center - margin), 12), round(min(1.0, center + margin), 12))


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include a UTC offset: {value!r}")
    return parsed


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _load_worker_result(path: Path) -> WorkerResult:
    payload = json.loads(path.read_text())
    if payload.get("schema_version") != WORKER_SCHEMA or not isinstance(
        payload.get("result"), dict
    ):
        raise ValueError(f"invalid coverage worker result: {path}")
    return WorkerResult(**payload["result"])


def _terminate_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _tail(path: Path, limit: int = 4000) -> str:
    try:
        return path.read_text(errors="replace")[-limit:].strip()
    except OSError:
        return ""
