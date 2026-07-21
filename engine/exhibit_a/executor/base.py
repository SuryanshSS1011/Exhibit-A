"""The Executor interface — the swappable sandbox boundary.

The plan (§2, Recommendation 3) is explicit: ship a plain Docker executor for the
hackathon, but hide it behind an interface so E2B/Daytona can drop in for v1 with
no change to the engine. Every executor takes a repo checkout + a test file and
runs it in isolation, returning raw logs. It never interprets results — that is
the verdict layer's job (which trusts execution logs over anything a model claims,
per AnyPoC, §3).

Security posture the interface assumes (enforced by concrete executors):
  - the test file is the ONLY writable path; source is read-only
  - no network by default
  - hard wall-clock + resource budget per run; timeout => a failed run, never a guess
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RepoState:
    """A concrete checkout to run against."""

    path: str  # local path to the checked-out repo
    label: str  # "base" | "target"
    commit: Optional[str] = None  # informational
    source: Optional[str] = None  # stable repo identity for environment caches


class EnvironmentSetupError(RuntimeError):
    """The sandbox environment could not be built deterministically."""


@dataclass(frozen=True)
class ExecSpec:
    """Everything needed to run one test in the sandbox."""

    test_path: str  # path (repo-relative) where the test file is written
    test_code: str  # source to write there
    command: str  # e.g. "pytest -x -q tests/test_repro.py"
    timeout_s: int = 120
    network: bool = False
    # image is optional: Docker executor caches one image per repo (SWE-smith pattern)
    image: Optional[str] = None


@dataclass
class ExecOutcome:
    """Raw result of one execution. Deliberately un-interpreted."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration_s: float = 0.0

    @property
    def log(self) -> str:
        if self.stderr:
            return f"{self.stdout}\n{self.stderr}".strip()
        return self.stdout

    @property
    def passed(self) -> bool:
        """A run passed iff it exited cleanly and did not time out."""
        return self.exit_code == 0 and not self.timed_out


class Executor(abc.ABC):
    """Runs untrusted, model-generated test code in isolation."""

    @abc.abstractmethod
    def prepare(self, repo: RepoState) -> Optional[str]:
        """Build/cache the environment for a repo state.

        Returns an opaque image/handle id the caller passes back via ExecSpec.image,
        or None if the executor prepares lazily. Should be idempotent and cache by
        repo content so repeated runs are cheap.
        """

    @abc.abstractmethod
    def run(self, repo: RepoState, spec: ExecSpec) -> ExecOutcome:
        """Write the test file into the checkout and run `spec.command` in isolation."""

    def close(self) -> None:  # optional cleanup hook
        """Tear down any persistent resources. Default: no-op."""
