"""Untrusted counterpatch proposal boundary for triangulation studies."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .generator import CodexGenerator

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidate"],
    "properties": {
        "candidate": {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["patch", "rationale"],
                    "properties": {
                        "patch": {"type": "string"},
                        "rationale": {"type": "string"},
                    },
                },
                {"type": "null"},
            ]
        }
    },
}


@dataclass(frozen=True)
class CounterpatchCandidate:
    patch: str
    rationale: str


class CounterpatchGenerator(Protocol):
    def propose(
        self, case: dict, repo_path: str, allowed_sources: tuple[str, ...]
    ) -> CounterpatchCandidate | None: ...


class CodexCounterpatchGenerator:
    def __init__(self, codex: CodexGenerator | None = None):
        self.codex = codex or CodexGenerator()
        self.last_error: str | None = None

    def propose(
        self, case: dict, repo_path: str, allowed_sources: tuple[str, ...]
    ) -> CounterpatchCandidate | None:
        prompt = f"""You are proposing an UNTRUSTED counterfactual patch for Exhibit A.

Work read-only. Return one minimal unified diff, or null. The diff may modify only these
production files: {", ".join(allowed_sources)}. Do not modify tests, configuration,
dependencies, generated files, or more than the behavior needed for the frozen test.
The patch is only a hypothesis: an external deterministic harness will apply it to a
disposable copy, rerun the frozen test, and run the full suite.

CLAIM:
{case.get("claim_text", "")}

ROOT-CAUSE NARRATIVE:
{case.get("root_cause_narrative", "")}

FROZEN TEST:
{case.get("test_file", {}).get("code", "")}
"""
        try:
            payload = self.codex._invoke(Path(repo_path), prompt, _SCHEMA)
            raw = payload.get("candidate")
            if raw is None:
                return None
            return CounterpatchCandidate(str(raw["patch"]), str(raw["rationale"]))
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            self.last_error = f"counterpatch generation failed: {exc}"
            return None
