"""The hypothesis generator boundary — where Codex/GPT plugs in.

The plan drives the model loop with Codex CLI (GPT-5.6). This module does NOT call
any provider directly; it defines the protocol the engine expects and ships a
deterministic stub so the whole pipeline runs offline. You (pair-programming with
Codex) implement a real `HypothesisGenerator` that shells out to / drives Codex.

The loop the engine wants from a generator (plan §2 "Hypothesis generator"):
  Localize -> Plan (1-3 falsifiable hypotheses) -> Draft passing test
  -> Invert (pass-then-invert, AssertFlip) -> [engine executes] -> Judge
  -> Refine on execution feedback (bounded retries) -> Verdict.

The engine owns Execute/Judge (the flip check). The generator owns
Localize/Plan/Draft/Invert/Refine. Keeping that split is what lets the
deterministic verdict layer stay honest regardless of how smart the model is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass
class Claim:
    """The input to the engine: a bug report, stack trace, or PR-diff concern."""

    text: str  # the raw claim / stack trace / issue body
    repo_path: str  # local checkout to reason over
    expected_signature: Optional[str] = None  # if the claim names an exception type


@dataclass
class Candidate:
    """One attempt from the generator: a test plus its metadata."""

    hypothesis: str  # the falsifiable guess this test probes
    test_path: str  # repo-relative path to write the test
    test_code: str  # the (inverted, fail-on-bug) test source
    run_command: str = "pytest -x -q"
    expected_signature: Optional[str] = None
    notes: str = ""  # localization / reasoning breadcrumbs for the UI


@dataclass
class Feedback:
    """What the engine hands back after executing a candidate, to drive refinement."""

    candidate: Candidate
    fail_log: str
    passed_on_target: bool
    admissible: bool
    reason: Optional[str] = None
    rejected_hypotheses: list[str] = field(default_factory=list)


class HypothesisGenerator(Protocol):
    """Contract for the model-driven part of the loop."""

    def propose(self, claim: Claim, max_hypotheses: int = 3) -> list[Candidate]:
        """Localize + plan + draft-and-invert into ranked candidate tests."""
        ...

    def refine(self, claim: Claim, feedback: Feedback) -> Optional[Candidate]:
        """Given execution feedback on a failed attempt, produce a better candidate,
        or None to give up (which the engine records as a silence_reason)."""
        ...


class StubGenerator:
    """Offline stand-in so the pipeline runs without a model.

    It emits a single trivial candidate that fails deterministically. Useful for
    exercising the executor + flip check on the built-in fixtures. Replace with a
    Codex-backed generator for real reproduction.
    """

    def propose(self, claim: Claim, max_hypotheses: int = 3) -> list[Candidate]:
        return [
            Candidate(
                hypothesis="stub: placeholder hypothesis (no model wired)",
                test_path="tests/test_exhibit_a_stub.py",
                test_code=(
                    "def test_stub_fails():\n"
                    "    # Deterministic failure so the flip check has something to chew on.\n"
                    "    assert False, 'stub generator: wire a real HypothesisGenerator'\n"
                ),
                expected_signature="AssertionError",
                notes="StubGenerator — replace with a Codex-driven generator.",
            )
        ]

    def refine(self, claim: Claim, feedback: Feedback) -> Optional[Candidate]:
        return None
