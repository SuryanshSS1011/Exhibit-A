import json

from exhibit_a.models.case import Case, Disposition, Mode, Verdict, normalize_case_payload
from exhibit_a.operations.policy import annotate_suite_gap, semantic_precision, should_trigger
from exhibit_a.store.json_store import JsonCaseStore


def test_webhook_policy_is_explicit_and_never_runs_on_synchronize():
    assert should_trigger("issue_comment", comment="/exhibit-a review")
    assert should_trigger("issue_comment", comment="Context\n/exhibit-a review\n")
    assert should_trigger("pull_request", action="ready_for_review")
    assert not should_trigger("pull_request", action="synchronize")
    assert not should_trigger("push")
    assert not should_trigger("issue_comment", comment="please review this")


def test_suite_gap_uses_external_ci_signal_without_changing_verdict():
    case = Case(id="case", mode=Mode.PROSECUTOR, verdict=Verdict.VERIFIED)

    annotate_suite_gap(case, existing_suite_passed=True)

    assert case.verdict is Verdict.VERIFIED
    assert case.existing_suite_passed is True
    assert case.suite_gap is True


def test_suite_gap_is_false_when_existing_tests_already_fail():
    case = Case(id="case", mode=Mode.PROSECUTOR, verdict=Verdict.VERIFIED)

    annotate_suite_gap(case, existing_suite_passed=False)

    assert case.suite_gap is False


def test_semantic_precision_requires_human_labels():
    report = semantic_precision([True, False, True])
    empty = semantic_precision([])

    assert report.human_judged_flags == 3
    assert report.confirmed_regressions == 2
    assert report.precision == 2 / 3
    assert empty.precision is None


def test_legacy_verdict_api_aliases_serialize_canonically():
    assert Verdict.PROVEN is Verdict.VERIFIED
    assert Verdict.REPRODUCED.value == "PARTIAL"
    assert Verdict.INSUFFICIENT_EVIDENCE.value == "UNCERTAIN"
    assert Disposition.REPRODUCED.value == "PARTIAL"
    assert Disposition.INSUFFICIENT_EVIDENCE.value == "UNCERTAIN"
    assert Verdict("PROVEN") is Verdict.VERIFIED
    assert Verdict("REPRODUCED") is Verdict.PARTIAL
    assert Verdict("INSUFFICIENT_EVIDENCE") is Verdict.UNCERTAIN
    assert Disposition("REPRODUCED") is Disposition.PARTIAL
    assert Disposition("INSUFFICIENT_EVIDENCE") is Disposition.UNCERTAIN


def test_legacy_case_payload_values_are_upgraded_together():
    assert normalize_case_payload({"verdict": "REPRODUCED", "disposition": "REPRODUCED"}) == {
        "verdict": "PARTIAL",
        "disposition": "PARTIAL",
    }


def test_case_store_upgrades_legacy_values_on_read(tmp_path):
    store = JsonCaseStore(tmp_path)
    (tmp_path / "legacy.json").write_text(
        json.dumps(
            {
                "id": "legacy",
                "verdict": "INSUFFICIENT_EVIDENCE",
                "disposition": "INSUFFICIENT_EVIDENCE",
            }
        )
    )

    assert store.load("legacy")["verdict"] == "UNCERTAIN"
    assert store.all()[0]["disposition"] == "UNCERTAIN"
    assert [case["id"] for case in store.silence_log()] == ["legacy"]
