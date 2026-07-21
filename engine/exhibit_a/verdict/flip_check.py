"""The Flip Check + validity gates — the hard "evidence-or-silence" boundary.

This module is pure, deterministic policy with NO model in the loop. It decides
whether a candidate test is admissible evidence. Per the plan (§3):

A test is admissible evidence IFF:
  (a) it FAILS on the target (buggy) state, with the EXPECTED failure signature;
  (b) it PASSES on the base/fixed state;
  (c) it is DETERMINISTIC across N reruns;
  (d) it did not tamper with the harness.

The checker trusts its own execution logs over anything the generator claims
(AnyPoC rule). If any gate fails, the verdict is INSUFFICIENT_EVIDENCE and a
silence_reason is recorded — never a guess.

`expected_signature` guards the "right failure for the wrong reason" trap
(AssertFlip / SWE-Doctor): a test that fails for an unrelated reason is NOT
evidence for the claim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ..executor.base import ExecOutcome
from .diff_location import ChangedLines, traceback_touches_changed_lines

# --- failure signature extraction -------------------------------------------

# pytest prints failures two ways on `E   ` lines:
#   - a raised exception:      `E   KeyError: 'missing'`   -> Uppercase-initial, dotted type
#   - a bare `assert` failure: `E   assert [3] == [3, 4]`  -> lowercase keyword, IS an AssertionError
# We match the exception form (requiring an uppercase-initial identifier so the
# lowercase `assert` keyword doesn't get mistaken for an exception type), and treat
# a bare-assert line as AssertionError. The short-test-summary line is a fallback.
_EXC_LINE = re.compile(r"^E\s+([A-Z][\w.]*(?:Error|Exception|Warning|Exit)?)\s*:\s*(.*)$", re.M)
_ASSERT_LINE = re.compile(r"^E\s+assert\b\s*(.*)$", re.M)
_SUMMARY = re.compile(r"^(FAILED|ERROR)\s+(\S+)(?:\s+-\s+(.*))?$", re.M)


def extract_signature(outcome: ExecOutcome) -> Optional[str]:
    """Pull a stable failure signature (exception type + short message) from logs.

    Returns None when nothing failure-like is found (e.g. a passing run).
    """
    log = outcome.log
    m = _EXC_LINE.search(log)
    if m:
        exc_type = m.group(1).strip()
        msg = m.group(2).strip()
        return f"{exc_type}: {msg}" if msg else exc_type
    m = _ASSERT_LINE.search(log)
    if m:
        detail = m.group(1).strip()
        return f"AssertionError: {detail}" if detail else "AssertionError"
    m = _SUMMARY.search(log)
    if m and m.group(3):
        return m.group(3).strip()
    return None


def signatures_match(expected: Optional[str], actual: Optional[str]) -> bool:
    """Loose match: the claimed failure reason must appear in the actual one.

    We require the expected exception TYPE (first token before ':') to be present.
    A full-string match is too brittle across message wording; a substring of the
    type name is the right granularity for "failed for the reason claimed."
    """
    if expected is None:
        return True  # caller didn't constrain the signature
    if actual is None:
        return False
    exp_type = expected.split(":", 1)[0].strip()
    return exp_type.lower() in actual.lower()


# --- harness-tamper detection ------------------------------------------------

# Echo documented agents trying to recreate/modify files or run unrelated tests
# to fake a pass. These markers in a test are red flags. This is a coarse static
# guard for the MVP; the Docker executor's read-only-source mount is the real
# enforcement in v1.
_TAMPER_PATTERNS = [
    r"\bos\.remove\b",
    r"\bshutil\.rmtree\b",
    r"\bopen\([^)]*['\"][wa]['\"]",  # opening non-test files for write
    r"\bsubprocess\b",
    r"\bmonkeypatch\.setattr\([^)]*__",  # patching dunders to force a pass
    r"\bsys\.exit\b",
]


def detect_tamper(test_code: str) -> Optional[str]:
    """Return a reason string if the test looks like it tampers with the harness."""
    for pat in _TAMPER_PATTERNS:
        if re.search(pat, test_code):
            return f"test contains a harness-tamper pattern: /{pat}/"
    return None


# --- vacuous-test detection (cheap anti-gaming) ------------------------------

# A test can "flip" trivially without exercising the code under test — e.g. by
# asserting on a renamed literal or a hardcoded constant. Full meaningfulness
# checking is mutation testing (v1+), but a cheap floor is: the test must actually
# reference the repository under test (an import or attribute access), and must
# contain at least one assertion. This blocks the most obvious way to game the gate.
_IMPORT_RE = re.compile(r"^\s*(?:from\s+\S+\s+import\b|import\s+\S+)", re.M)
_ASSERTISH_RE = re.compile(r"\b(assert|pytest\.raises|self\.assert\w+)\b")
# Imports that don't count as "exercising the repo" — stdlib/test-framework only.
_INERT_IMPORTS = {"pytest", "unittest", "sys", "os", "re", "math", "json", "typing"}


def detect_vacuous(test_code: str) -> Optional[str]:
    """Return a reason if the test looks vacuous (cannot be real evidence).

    Cheap, deterministic floor beneath the flip check — NOT a substitute for mutation
    testing. Catches: no assertion at all, or no import that reaches the code under
    test (only stdlib/pytest imports), which is how a "renamed-string" gaming test
    would look.
    """
    if not _ASSERTISH_RE.search(test_code):
        return "test contains no assertion — cannot be evidence"

    imports = _IMPORT_RE.findall(test_code)
    # Extract the top-level module of each import line.
    roots: set[str] = set()
    for line in imports:
        m = re.search(r"(?:from|import)\s+([A-Za-z_][\w.]*)", line)
        if m:
            roots.add(m.group(1).split(".")[0])
    if not roots:
        return "test imports nothing — it cannot exercise the code under test"
    if roots.issubset(_INERT_IMPORTS):
        return "test imports only stdlib/pytest — it does not exercise the repository under test"
    return None


# --- infrastructure-failure detection ----------------------------------------

# The "fail for the wrong reason" trap (SWE-Doctor 2026): a candidate can "fail on
# target, pass on base" for reasons that have nothing to do with the claimed bug —
# a missing dependency, a broken import, a pytest collection error, a syntax error
# in the generated test. These are HARNESS/ENVIRONMENT failures, not evidence.
#
# We reject them regardless of whether an expected_signature was supplied, because
# the most dangerous case is Detective mode with expected_signature=None, where an
# env failure would otherwise sail through as PROVEN. A signature naming the bug is
# a *positive* filter; this is the *negative* one that must always run.
_INFRA_SIGNATURES = (
    "ModuleNotFoundError",
    "ImportError",
    "collection error",
    "errors during collection",
    "INTERNALERROR",
    "no tests ran",
    "no tests collected",
    "usage error",
    "unrecognized arguments",
    "fixture ",  # e.g. "fixture 'foo' not found"
    "SyntaxError",
    "IndentationError",
    "ERROR collecting",
)


def detect_infra_failure(outcome: ExecOutcome) -> Optional[str]:
    """Return a reason if a run failed for an environmental/harness reason.

    This is the SWE-Doctor "right failure, wrong reason" guard applied to the
    *infrastructure* axis: a repro is only evidence if the code under test raised,
    not if the environment failed to load it. pytest's exit code 2 signals a
    collection/usage error (as opposed to 1 = tests failed), which is a strong
    infra-failure signal on its own.
    """
    log = outcome.log
    # pytest: exit 1 = tests failed (legitimate), exit 2 = collection/usage error.
    if outcome.exit_code == 2 and "failed" not in log.lower():
        return "pytest reported a collection/usage error (exit code 2), not a test failure"
    lowered = log.lower()
    for marker in _INFRA_SIGNATURES:
        if marker.lower() in lowered:
            return f"target failed for an environmental/harness reason ({marker.strip()}), not the claimed bug"
    return None


# --- the flip check ----------------------------------------------------------


@dataclass
class FlipResult:
    admissible: bool
    reason: Optional[str]  # silence_reason when not admissible; None when admissible
    fail_signature: Optional[str] = None
    deterministic: bool = False
    # Evidence tier for an admissible result:
    #   "flip"       -> full fail-on-target + pass-on-base (maps to Verdict.PROVEN)
    #   "reproduced" -> deterministic, signature-matched failure with NO pass state
    #                   (maps to Verdict.REPRODUCED — weaker, honest about the gap)
    # None when not admissible.
    tier: Optional[str] = None


def flip_check(
    *,
    target_runs: list[ExecOutcome],
    base_run: Optional[ExecOutcome],
    test_code: str,
    expected_signature: Optional[str] = None,
    allow_reproduced: bool = False,
    changed_lines: ChangedLines | None = None,
    control_run: Optional[ExecOutcome] = None,
) -> FlipResult:
    """Apply all admissibility gates. `target_runs` are N reruns on the buggy state.

    base_run may be None in Detective BASE_ONLY mode, where we cannot prove the pass
    side (a fresh production bug exists on every recent commit — there is no fixed
    state to pass on). Handling of that case:

    - `allow_reproduced=False` (default, the honest strict path): without a base run
      we return admissible=False. A flip we could not run is not a proven regression.
    - `allow_reproduced=True` AND an `expected_signature` was supplied AND the target
      fails deterministically with that signature: we return admissible with
      tier="reproduced" -> Verdict.REPRODUCED. This is signature-matched reproduction,
      explicitly weaker than a full flip, and requires a signature so a vacuous
      `assert False` cannot "reproduce" an arbitrary claim.
    """
    if not target_runs:
        return FlipResult(False, "no target execution recorded")

    # (d) tamper gate — cheapest, run first.
    tamper = detect_tamper(test_code)
    if tamper:
        return FlipResult(False, tamper)

    # (d') vacuous-test gate — cheap anti-gaming floor. A test that asserts on a
    # renamed literal (no import of the code under test) can flip trivially; reject it.
    vacuous = detect_vacuous(test_code)
    if vacuous:
        return FlipResult(False, vacuous)

    # (a) fail-on-target: every rerun must fail.
    target_failed = [not r.passed for r in target_runs]
    if not all(target_failed):
        # Non-deterministic or didn't fail at all.
        if any(target_failed):
            return FlipResult(
                False,
                f"flaky on target: failed {sum(target_failed)}/{len(target_runs)} reruns",
                deterministic=False,
            )
        return FlipResult(False, "test does not fail on the target (buggy) state")

    # (a'') infra-failure gate — the target must fail because the CODE raised, not
    # because the environment/harness broke (missing dep, import/collection/syntax
    # error). This runs even when no expected_signature is supplied, closing the
    # env-failure -> false-PROVEN hole (SWE-Doctor "fail for the wrong reason").
    infra = detect_infra_failure(target_runs[0])
    if infra:
        return FlipResult(
            False,
            infra,
            fail_signature=extract_signature(target_runs[0]),
            deterministic=all(target_failed),
        )

    # (a') signature match — failed for the reason claimed.
    actual_sig = extract_signature(target_runs[0])
    if not signatures_match(expected_signature, actual_sig):
        return FlipResult(
            False,
            f"failed for the wrong reason: expected ~{expected_signature!r}, got {actual_sig!r}",
            fail_signature=actual_sig,
        )

    if changed_lines is not None and not traceback_touches_changed_lines(
        target_runs[0], changed_lines
    ):
        return FlipResult(
            False,
            "failure traceback does not touch a changed PR line or a deterministic downstream path",
            fail_signature=actual_sig,
            deterministic=all(target_failed),
        )

    # (c) determinism confirmed for the fail side.
    deterministic = len(target_runs) >= 1 and all(target_failed)

    # (b) pass-on-base.
    if base_run is None:
        # No fixed state to flip against. Only offer the weaker REPRODUCED tier, and
        # only when the caller opted in AND the failure is signature-matched (so a
        # vacuous failure cannot masquerade as a reproduction of a specific claim).
        if allow_reproduced and expected_signature is not None:
            return FlipResult(
                admissible=True,
                reason=None,
                fail_signature=actual_sig,
                deterministic=deterministic,
                tier="reproduced",
            )
        return FlipResult(
            False,
            "confirmed deterministic failure on target, but no base/fixed state to prove the pass side",
            fail_signature=actual_sig,
            deterministic=deterministic,
        )
    if not base_run.passed:
        return FlipResult(
            False,
            "test does not pass on the base/fixed state (fail-to-fail, not fail-to-pass)",
            fail_signature=actual_sig,
            deterministic=deterministic,
        )

    # Cheap anti-coupling check: evidence for this delta should survive on a caller-
    # selected older/unrelated state. A failure there means the candidate is coupled
    # to pre-existing or incidental behavior rather than isolating this change.
    if control_run is not None and not control_run.passed:
        control_infra = detect_infra_failure(control_run)
        reason = control_infra or (
            "test also fails on the unrelated control state; candidate is not specific to the claimed change"
        )
        return FlipResult(
            False,
            reason,
            fail_signature=actual_sig,
            deterministic=deterministic,
        )

    # All gates cleared — full flip.
    return FlipResult(
        admissible=True,
        reason=None,
        fail_signature=actual_sig,
        deterministic=deterministic,
        tier="flip",
    )
