"""Separate, fallible intent assessment layered above deterministic evidence."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..models.case import IntentJudgment
from .generator import CodexGenerator

_INTENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["judgment", "rationale"],
    "properties": {
        "judgment": {"type": "string", "enum": ["intended", "unintended", "unclear"]},
        "rationale": {"type": "string"},
    },
}


@dataclass(frozen=True)
class IntentAssessment:
    judgment: IntentJudgment
    rationale: str
    model: str


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
description and linked-issue context below and classify only whether the described
delta appears intended, unintended, or unclear. Choose unclear whenever the text is
ambiguous or silent. Give a short rationale grounded only in that context.

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
            return IntentAssessment(judgment, rationale, self.generator.model)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            self.last_error = f"Intent assessment failed: {exc}"
            return None
