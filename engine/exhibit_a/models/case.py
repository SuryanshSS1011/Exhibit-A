"""The Case data model — the single artifact the Evidence Engine emits.

Mirrors the schema in the plan (§2 "Test artifact output") and carries the
SWE-bench-compatible fields needed for the open-science minting layer (§5).

Nothing in here does work; it is the shared contract every layer speaks. Keep it
serialization-friendly (dataclasses -> dict) so the web layer and the dataset
publisher can consume the same object without a translation shim.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


class Mode(str, enum.Enum):
    PROSECUTOR = "prosecutor"  # reviews a PR: comment only on a proven flip
    DETECTIVE = "detective"  # reproduces a report into a verified failing test


class Verdict(str, enum.Enum):
    # A full flip: the test FAILS on target and PASSES on a base/fixed state, with a
    # matching, deterministic failure signature. The strongest evidence tier.
    PROVEN = "PROVEN"
    # A signature-matched reproduction WITHOUT a proven pass state (the common
    # Detective case: a fresh bug exists on every recent commit, so there is no
    # fixed state to pass on). Deterministic + signature-matched, but materially
    # weaker than PROVEN — we do not overclaim a flip we could not run.
    REPRODUCED = "REPRODUCED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class TargetKind(str, enum.Enum):
    """Which second state we compare the base against."""

    PR_HEAD = "pr_head"  # Prosecutor: the PR's proposed code
    SYNTHESIZED_PATCH = "synthesized_patch"  # Detective: a fix we generated
    BASE_ONLY = "base_only"  # Detective on a single buggy checkout


class IntentJudgment(str, enum.Enum):
    NOT_ASSESSED = "not_assessed"
    INTENDED = "intended"
    UNINTENDED = "unintended"
    UNCLEAR = "unclear"


@dataclass
class TestArtifact:
    """The candidate test itself."""

    path: str  # where the test file lives in the repo, e.g. tests/test_repro.py
    code: str  # full source of the test file
    language: str = "python"
    framework: str = "pytest"


@dataclass
class RunResult:
    """One execution of the test file in a sandbox against one code state."""

    state: str  # "base" | "target" | "control"
    exit_code: int
    passed: bool  # whether the test PASSED in this run
    log: str  # raw captured stdout/stderr (never paraphrased)
    signature: Optional[str] = None  # extracted failure signature (exc type + msg), if any
    duration_s: Optional[float] = None


@dataclass
class Evidence:
    """The contrast that makes a claim admissible: fail on target, pass on base."""

    fail_log: str = ""
    fail_signature: Optional[str] = None
    pass_log: str = ""
    control_log: str = ""
    reruns: int = 0
    deterministic: bool = False
    # Full per-run records, useful for the UI's two-tab evidence panel.
    runs: list[RunResult] = field(default_factory=list)


@dataclass
class Hypothesis:
    """A single falsifiable guess produced by the hypothesis generator."""

    text: str
    rejected: bool = False
    reason: Optional[str] = None  # why rejected, for the "greyed out" honesty UI


@dataclass
class Case:
    """The Case File — the whole product's output artifact."""

    id: str
    mode: Mode

    # --- provenance ---
    repo: Optional[str] = None  # url or local path
    base_commit: Optional[str] = None
    target_commit: Optional[str] = None
    target_state: TargetKind = TargetKind.BASE_ONLY

    # --- the claim & reasoning ---
    claim_text: str = ""
    hypotheses: list[Hypothesis] = field(default_factory=list)
    root_cause_narrative: str = ""
    # Separate, fallible interpretation of PR/issue intent. Never part of PROVEN.
    intent_judgment: IntentJudgment = IntentJudgment.NOT_ASSESSED
    intent_rationale: Optional[str] = None
    intent_model: Optional[str] = None

    # --- the evidence ---
    test_file: Optional[TestArtifact] = None
    run_command: str = "pytest -x -q"
    evidence: Evidence = field(default_factory=Evidence)

    # --- the verdict ---
    verdict: Verdict = Verdict.INSUFFICIENT_EVIDENCE
    silence_reason: Optional[str] = None  # populated iff INSUFFICIENT_EVIDENCE

    # --- open-science / benchmark fields (SWE-bench compatible) ---
    license_name: Optional[str] = None  # SPDX of source repo @ commit
    fail_to_pass: list[str] = field(default_factory=list)  # test node ids
    pass_to_pass: list[str] = field(default_factory=list)

    # External CI comparison. The evidence engine never runs an out-of-scope suite.
    existing_suite_passed: Optional[bool] = None
    suite_gap: Optional[bool] = None

    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def is_proven(self) -> bool:
        """True only for a full flip (PROVEN). REPRODUCED is deliberately excluded —
        it is admissible evidence but not a proven regression."""
        return self.verdict is Verdict.PROVEN

    def is_evidence(self) -> bool:
        """True for any admissible verdict tier (PROVEN or REPRODUCED)."""
        return self.verdict in (Verdict.PROVEN, Verdict.REPRODUCED)

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form for JSON / API / dataset export."""
        return asdict(self)
