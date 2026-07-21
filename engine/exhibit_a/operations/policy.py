"""Deterministic webhook economics and evaluation metrics."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ..models.case import Case

_REVIEW_COMMAND = re.compile(r"(?m)^\s*/exhibit-a review\s*$")


def should_trigger(event: str, *, action: str | None = None, comment: str | None = None) -> bool:
    """Allow only explicit review requests or the ready-for-review transition."""
    if event == "pull_request":
        return action == "ready_for_review"
    if event == "issue_comment" and comment is not None:
        return bool(_REVIEW_COMMAND.search(comment))
    return False


def annotate_suite_gap(case: Case, *, existing_suite_passed: bool) -> None:
    """Attach an external CI signal without running any out-of-scope tests."""
    case.existing_suite_passed = existing_suite_passed
    case.suite_gap = case.is_evidence() and existing_suite_passed


@dataclass(frozen=True)
class SemanticPrecision:
    human_judged_flags: int
    confirmed_regressions: int
    precision: float | None


def semantic_precision(human_labels: Iterable[bool]) -> SemanticPrecision:
    """Headline metric: confirmed regressions among human-judged flagged deltas."""
    labels = tuple(human_labels)
    confirmed = sum(labels)
    return SemanticPrecision(
        human_judged_flags=len(labels),
        confirmed_regressions=confirmed,
        precision=confirmed / len(labels) if labels else None,
    )
