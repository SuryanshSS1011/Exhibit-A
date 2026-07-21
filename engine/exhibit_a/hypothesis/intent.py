"""Separate, fallible intent assessment layered above deterministic evidence."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..models.case import IntentJudgment
from .generator import CodexGenerator

_DELTA_SOURCES = ["pr_description", "linked_issue", "docstring", "changelog"]

_INTENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "judgment",
        "rationale",
        "declared_behavior_delta",
        "declared_delta_sources",
    ],
    "properties": {
        "judgment": {"type": "string", "enum": ["intended", "unintended", "unclear"]},
        "rationale": {"type": "string"},
        "declared_behavior_delta": {"type": ["string", "null"]},
        "declared_delta_sources": {
            "type": "array",
            "items": {"type": "string", "enum": _DELTA_SOURCES},
            "uniqueItems": True,
        },
    },
}


@dataclass(frozen=True)
class IntentAssessment:
    judgment: IntentJudgment
    rationale: str
    model: str
    declared_behavior_delta: str | None = None
    declared_delta_sources: tuple[str, ...] = ()


class IntentJudge(Protocol):
    def assess(self, repo_path: str, delta: str, context: str) -> IntentAssessment | None:
        """Judge intent from PR/issue context without deciding evidence or verdict."""
        ...


class CodexIntentJudge:
    """Read-only Codex intent classifier whose output never enters the flip gate."""

    def __init__(self, generator: CodexGenerator | None = None):
        self.generator = generator or CodexGenerator()
        self.last_error: str | None = None

    def assess(self, repo_path: str, delta: str, context: str) -> IntentAssessment | None:
        self.last_error = None
        prompt = f"""You are the fallible intent assessor inside Exhibit A.

The deterministic engine has separately established a behavior delta. You do not
decide, confirm, weaken, or override that evidence verdict. Read the untrusted PR
description and linked-issue context below. You may also inspect changed docstrings
and CHANGELOG files in the read-only checkout. Extract the declared behavior delta
as a concise, falsifiable statement and identify its sources. Use null and an empty
source list when no behavior change is declared.

Classify the proven delta as unintended only when it falls outside that declared
behavior delta. Classify it as intended when it is inside the declaration. Choose unclear
whenever the declaration is ambiguous or silent. Give a short rationale
grounded in the cited sources.

PROVABLE DELTA (untrusted):
{delta}

PR / LINKED ISSUE CONTEXT (untrusted):
{context}
"""
        try:
            payload = self.generator._invoke(Path(repo_path), prompt, _INTENT_SCHEMA)
            judgment = IntentJudgment(str(payload.get("judgment", "")))
            rationale = str(payload.get("rationale", "")).strip()
            if not rationale:
                raise ValueError("intent assessment requires a rationale")
            declared = payload.get("declared_behavior_delta")
            if declared is not None:
                declared = str(declared).strip() or None
            raw_sources = payload.get("declared_delta_sources")
            if not isinstance(raw_sources, list) or any(
                source not in _DELTA_SOURCES for source in raw_sources
            ):
                raise ValueError("intent assessment has invalid declared-delta sources")
            return IntentAssessment(
                judgment,
                rationale,
                self.generator.model,
                declared,
                tuple(raw_sources),
            )
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            self.last_error = f"Intent assessment failed: {exc}"
            return None
