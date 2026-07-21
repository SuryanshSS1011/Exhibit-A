"""Transparent executor instrumentation for environment-setup research."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .base import ExecOutcome, ExecSpec, Executor, RepoState, SourceMutation
from ..store.suite_gap import ENGINE_VERSION

SCHEMA_VERSION = "environment-attempt/v1"
_LOCKFILES = (
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "requirements.txt",
    "requirements-dev.txt",
    "pylock.toml",
)


@dataclass(frozen=True)
class EnvironmentAttempt:
    schema_version: str
    engine_version: str
    id: str
    recorded_at: str
    repo_source: str
    state_label: str
    commit: str | None
    executor: str
    strategy: str
    repo_shape: tuple[str, ...]
    succeeded: bool
    environment_ref: str | None
    duration_s: float
    error_type: str | None
    error: str | None


class RecordingExecutor(Executor):
    """Delegate execution unchanged while recording every explicit prepare attempt."""

    def __init__(self, inner: Executor, root: str | Path):
        self.inner = inner
        self.root = Path(root)

    def __getattr__(self, name: str):
        return getattr(self.inner, name)

    def prepare(self, repo: RepoState) -> str | None:
        start = time.monotonic()
        environment_ref: str | None = None
        error: BaseException | None = None
        try:
            environment_ref = self.inner.prepare(repo)
            return environment_ref
        except BaseException as exc:
            error = exc
            raise
        finally:
            self._record(repo, environment_ref, time.monotonic() - start, error)

    def run(self, repo: RepoState, spec: ExecSpec) -> ExecOutcome:
        return self.inner.run(repo, spec)

    def run_suite(
        self,
        repo: RepoState,
        argv: list[str],
        *,
        image: str | None = None,
        timeout_s: int = 120,
    ) -> ExecOutcome | None:
        return self.inner.run_suite(repo, argv, image=image, timeout_s=timeout_s)

    def run_mutant(
        self, repo: RepoState, spec: ExecSpec, mutation: SourceMutation
    ) -> ExecOutcome | None:
        return self.inner.run_mutant(repo, spec, mutation)

    def close(self) -> None:
        self.inner.close()

    def _record(
        self,
        repo: RepoState,
        environment_ref: str | None,
        duration_s: float,
        error: BaseException | None,
    ) -> None:
        shape = repository_shape(repo.path)
        attempt = EnvironmentAttempt(
            schema_version=SCHEMA_VERSION,
            engine_version=ENGINE_VERSION,
            id=uuid.uuid4().hex[:12],
            recorded_at=datetime.now(timezone.utc).isoformat(),
            repo_source=repo.source or repo.path,
            state_label=repo.label,
            commit=repo.commit,
            executor=type(self.inner).__name__,
            strategy=environment_strategy(type(self.inner).__name__, shape),
            repo_shape=shape,
            succeeded=error is None,
            environment_ref=environment_ref,
            duration_s=round(duration_s, 6),
            error_type=type(error).__name__ if error else None,
            error=str(error)[-2000:] if error else None,
        )
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{attempt.id}.json").write_text(
            json.dumps(asdict(attempt), indent=2, sort_keys=True)
        )


def repository_shape(repo_path: str | Path) -> tuple[str, ...]:
    root = Path(repo_path).resolve()
    if not root.is_dir():
        return ("missing-checkout",)
    markers = [name for name in _LOCKFILES if (root / name).is_file()]
    if (root / "pyproject.toml").is_file():
        markers.append("pyproject.toml")
    if (root / "setup.py").is_file() or (root / "setup.cfg").is_file():
        markers.append("setuptools")
    return tuple(markers) or ("stdlib-or-unknown",)


def environment_strategy(executor: str, shape: tuple[str, ...]) -> str:
    if executor == "LocalExecutor":
        return "host-existing-environment"
    lockfiles = [marker for marker in shape if marker in _LOCKFILES]
    return f"pinned-lock:{'+'.join(lockfiles)}" if lockfiles else "no-supported-lock"


def summarize_environment_attempts(root: str | Path) -> dict:
    """Aggregate empirical success rates by repository shape and strategy."""
    records = []
    for path in sorted(Path(root).glob("*.json")):
        payload = json.loads(path.read_text())
        if payload.get("schema_version") == SCHEMA_VERSION:
            records.append(payload)
    groups: dict[tuple[tuple[str, ...], str], list[bool]] = {}
    for record in records:
        key = (tuple(record["repo_shape"]), record["strategy"])
        groups.setdefault(key, []).append(bool(record["succeeded"]))
    recipes = [
        {
            "repo_shape": list(shape),
            "strategy": strategy,
            "attempts": len(outcomes),
            "successes": sum(outcomes),
            "success_rate": sum(outcomes) / len(outcomes),
        }
        for (shape, strategy), outcomes in sorted(groups.items())
    ]
    return {
        "schema_version": "environment-summary/v1",
        "engine_version": ENGINE_VERSION,
        "attempts": len(records),
        "recipes": recipes,
    }
