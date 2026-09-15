"""Render a Case into a pull-request comment, or into nothing at all.

The product's rule is a runnable fail-to-pass test or silence, and a comment is where
that rule is either kept or quietly broken. So this renders evidence -- the test, the
command that runs it, and both execution logs -- and returns ``None`` for everything
else. A reviewer that posts "no issues found" on every pull request is the alert fatigue
this project exists to avoid, wearing a politer face.

Scrubbing here is defence in depth and nothing stronger. It removes the host path shapes
and credential-shaped environment values we know of, and it cannot make hostile execution
output safe: a secret held in a variable named without one of those words, read from a
file, or encoded on the way out will survive it. The property that matters is upstream --
the container passes five named variables and no network, so a contributed test has no
credential to leak in the first place. A comment rendered from a host-executor run does
not have that property and should not be treated as publishable.
"""

from __future__ import annotations

import os
import re

from ..models.case import Case, Verdict

# Host paths leak the runner's filesystem and its user. Container paths under /work are
# not host paths and stay, because they are what the reproduce command refers to.
_SCRATCH = re.compile(r"\S*?/exhibit-a-[0-9A-Za-z_-]+/(?:repo/?)?")
_HOME = re.compile(r"/(?:Users|home|root)/[^/\s:\"']*/?")
_WINDOWS_PATH = re.compile(r"[A-Za-z]:\\\\(?:Users|Windows)\\\\[^\s:\"']*")
_TEMP_ROOT = re.compile(r"(?:/private)?/(?:var|tmp)/[^\s:\"']*")
_SECRET_NAME = re.compile(r"(?:API_?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.IGNORECASE)
_MIN_SECRET_LENGTH = 12
_REDACTED = "[redacted]"
_ELIDED = "[...]"
# Enough of a log to see the failure and its traceback; a comment is not an archive.
_MAX_LOG_CHARS = 2400
_MAX_TEST_CHARS = 4000


def render_review_comment(case: Case) -> str | None:
    """Return the comment this Case earns, or None when it has not earned one.

    Only a full flip earns one. A PARTIAL reproduction has no proven pass state, so on a
    pull request -- where the base revision is right there to compare against -- it is an
    inconclusive run rather than a finding.
    """
    if case.verdict is not Verdict.VERIFIED:
        return None
    if case.test_file is None or not case.evidence.fail_log:
        return None

    evidence = case.evidence
    lines = [
        "## Exhibit A: proven regression",
        "",
        "This comment exists because a test was found that **fails on this change and "
        "passes without it**. Nothing here is a model's opinion; the verdict comes from "
        "running the test on both revisions.",
        "",
    ]
    if case.claim_text.strip():
        lines += ["**Change under review:**", *_fence(_scrub(case.claim_text.strip())), ""]
    lines += [
        f"**Determinism:** failed on {evidence.reruns} of {evidence.reruns} runs of the "
        f"changed revision"
        + (" (deterministic)" if evidence.deterministic else " (not deterministic)"),
    ]
    if evidence.fail_signature:
        lines.append(f"**Failure:** `{_scrub(evidence.fail_signature)}`")
    if case.target_commit and case.base_commit:
        lines.append(
            f"**Revisions:** `{case.target_commit[:12]}` against `{case.base_commit[:12]}`"
        )
    lines += [
        "",
        f"### The test  (`{_scrub(case.test_file.path)}`)",
        "",
        *_fence(
            _clip(_scrub(case.test_file.code), _MAX_TEST_CHARS), case.test_file.language or "python"
        ),
        "",
        "### Reproduce it",
        "",
        *_fence(_scrub(case.run_command or "")),
        "",
        "### On this change it fails",
        "",
        *_fence(_clip(_scrub(evidence.fail_log), _MAX_LOG_CHARS)),
        "",
        "### Without this change it passes",
        "",
        *_fence(_clip(_scrub(evidence.pass_log), _MAX_LOG_CHARS)),
    ]
    if case.root_cause_narrative:
        lines += [
            "",
            "<details><summary>The model's hypothesis, which is not the evidence</summary>",
            "",
            *_fence(_scrub(case.root_cause_narrative)),
            "",
            "</details>",
        ]
    return "\n".join(lines) + "\n"


def _scrub(text: str) -> str:
    """Remove host paths and anything that looks like a credential."""
    scrubbed = _SCRATCH.sub("", text)
    scrubbed = _HOME.sub("", scrubbed)
    scrubbed = _WINDOWS_PATH.sub("", scrubbed)
    scrubbed = _TEMP_ROOT.sub("", scrubbed)
    for secret in _environment_secrets():
        scrubbed = scrubbed.replace(secret, _REDACTED)
    return scrubbed


def _environment_secrets() -> tuple[str, ...]:
    """Values of credential-shaped environment variables, longest first.

    Longest first so a short secret that is a substring of a longer one cannot leave the
    longer one partly intact.
    """
    return tuple(
        sorted(
            {
                value
                for name, value in os.environ.items()
                if _SECRET_NAME.search(name) and len(value) >= _MIN_SECRET_LENGTH
            },
            key=len,
            reverse=True,
        )
    )


def _fence(body: str, language: str = "") -> list[str]:
    """Fence untrusted text so it cannot close the fence and render as markup.

    Execution output is attacker-influenced. A log containing three backticks would end
    our block early and let everything after it render as Markdown or HTML.
    """
    longest = 0
    run = 0
    for character in body:
        run = run + 1 if character == "`" else 0
        longest = max(longest, run)
    ticks = "`" * max(3, longest + 1)
    return [f"{ticks}{language}", body, ticks]


def _clip(text: str, limit: int) -> str:
    """Keep the tail, which is where a pytest failure and its traceback live."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{_ELIDED}\n{text[-limit:]}"
