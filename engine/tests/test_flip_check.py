"""Unit tests for the deterministic flip check — the product's honesty core.

Focus: the "fail for the wrong reason" trap (SWE-Doctor 2026). A repro is only
evidence if the code under test raised — not if the environment/harness broke.
"""

from __future__ import annotations

from exhibit_a.executor.base import ExecOutcome
from exhibit_a.verdict.flip_check import (
    detect_infra_failure,
    detect_vacuous,
    extract_signature,
    flip_check,
    signatures_match,
)


def _out(passed: bool, log: str, exit_code: int | None = None) -> ExecOutcome:
    if exit_code is None:
        exit_code = 0 if passed else 1
    return ExecOutcome(exit_code=exit_code, stdout=log, stderr="", timed_out=False)


# A realistic (non-vacuous) test body: imports the code under test and asserts on it.
# Used wherever a test's *content* is not what's under test, so the vacuous-test gate
# (which requires a real import of the repo) doesn't reject the fixture.
REAL_TEST_CODE = "from slicer import last_n\n\ndef test_x():\n    assert last_n([1, 2], 1) == [2]\n"


# --- the env-failure -> false-PROVEN hole (regression) -----------------------

MODULE_ERR = (
    "ImportError while importing test module 'test_repro.py'.\n"
    "E   ModuleNotFoundError: No module named 'numpy'\n"
)


def test_env_failure_is_not_evidence_even_without_expected_signature():
    # target "fails" only because a dependency is missing; base "passes".
    # Without the infra gate this was stamped PROVEN — the exact SWE-Doctor trap.
    target = [_out(False, MODULE_ERR, exit_code=2) for _ in range(3)]
    base = _out(True, "1 passed")
    result = flip_check(
        target_runs=target, base_run=base, test_code=REAL_TEST_CODE, expected_signature=None
    )
    assert not result.admissible
    assert "environmental" in (result.reason or "") or "collection" in (result.reason or "")


def test_collection_error_exit_code_2_rejected():
    log = "test_repro.py::test_x ERROR\nE   fixture 'db' not found\n"
    target = [_out(False, log, exit_code=2) for _ in range(2)]
    base = _out(True, "1 passed")
    result = flip_check(
        target_runs=target, base_run=base, test_code=REAL_TEST_CODE, expected_signature=None
    )
    assert not result.admissible
    assert result.reason


def test_syntax_error_in_generated_test_rejected():
    log = "E   SyntaxError: invalid syntax\n"
    target = [_out(False, log, exit_code=2) for _ in range(2)]
    base = _out(True, "1 passed")
    result = flip_check(
        target_runs=target, base_run=base, test_code=REAL_TEST_CODE, expected_signature=None
    )
    assert not result.admissible


# --- a legitimate failure still passes the gate ------------------------------

REAL_FAIL = (
    "    def test_last_n_keeps_final_element():\n"
    ">       assert last_n([1, 2, 3, 4], 2) == [3, 4]\n"
    "E       assert [3] == [3, 4]\n"
)


def test_genuine_assertion_failure_is_admissible():
    target = [_out(False, REAL_FAIL, exit_code=1) for _ in range(3)]
    base = _out(True, "1 passed")
    result = flip_check(
        target_runs=target,
        base_run=base,
        test_code=REAL_TEST_CODE,
        expected_signature="AssertionError",
    )
    assert result.admissible, result.reason
    assert result.deterministic


def test_genuine_keyerror_admissible_and_not_flagged_infra():
    log = "E   KeyError: 'sku-404'\n"
    assert detect_infra_failure(_out(False, log, exit_code=1)) is None
    target = [_out(False, log, exit_code=1) for _ in range(3)]
    base = _out(True, "1 passed")
    result = flip_check(
        target_runs=target, base_run=base, test_code=REAL_TEST_CODE, expected_signature="KeyError"
    )
    assert result.admissible, result.reason


# --- signature helpers -------------------------------------------------------


def test_signature_mismatch_rejected():
    assert not signatures_match("KeyError", "ValueError: bad")
    assert signatures_match("KeyError", "KeyError: 'x'")
    assert signatures_match(None, "anything")  # opt-in: no constraint


def test_reproduced_tier_requires_optin_and_signature():
    log = "E   KeyError: 'sku-404'\n"
    target = [_out(False, log, exit_code=1) for _ in range(3)]

    # opt-out (default): no base -> not admissible.
    r_off = flip_check(
        target_runs=target, base_run=None, test_code=REAL_TEST_CODE, expected_signature="KeyError"
    )
    assert not r_off.admissible

    # opt-in WITH signature: admissible at the weaker "reproduced" tier.
    r_on = flip_check(
        target_runs=target,
        base_run=None,
        test_code=REAL_TEST_CODE,
        expected_signature="KeyError",
        allow_reproduced=True,
    )
    assert r_on.admissible and r_on.tier == "reproduced"

    # opt-in WITHOUT signature: refused — a vacuous failure must not "reproduce" a claim.
    r_nosig = flip_check(
        target_runs=target,
        base_run=None,
        test_code=REAL_TEST_CODE,
        expected_signature=None,
        allow_reproduced=True,
    )
    assert not r_nosig.admissible


def test_full_flip_is_tier_flip():
    target = [_out(False, REAL_FAIL, exit_code=1) for _ in range(3)]
    base = _out(True, "1 passed")
    r = flip_check(
        target_runs=target,
        base_run=base,
        test_code=REAL_TEST_CODE,
        expected_signature="AssertionError",
    )
    assert r.admissible and r.tier == "flip"


def test_vacuous_test_rejected_by_flip_check():
    # A test that flips but only asserts on a literal (no import of the repo) is the
    # classic way to game the gate — it must be rejected before it can be evidence.
    gaming = "def test_flip():\n    assert 'renamed' == 'renamed'\n"
    assert detect_vacuous(gaming) is not None
    target = [_out(False, REAL_FAIL, exit_code=1) for _ in range(3)]
    base = _out(True, "1 passed")
    r = flip_check(
        target_runs=target, base_run=base, test_code=gaming, expected_signature="AssertionError"
    )
    assert not r.admissible
    # No-assertion and stdlib-only-import cases are also caught.
    assert detect_vacuous("import os\n\ndef test_x():\n    x = 1\n") is not None
    assert detect_vacuous("import pytest\n\ndef test_x():\n    assert True\n") is not None
    # A real import + assertion is fine.
    assert detect_vacuous(REAL_TEST_CODE) is None


def test_extract_signature_forms():
    assert extract_signature(_out(False, "E   KeyError: 'x'\n")) == "KeyError: 'x'"
    assert extract_signature(_out(False, "E   assert [3] == [3, 4]\n")).startswith("AssertionError")
    assert extract_signature(_out(True, "1 passed")) is None
