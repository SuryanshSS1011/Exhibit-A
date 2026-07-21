from exhibit_a.models.case import Case, Mode, Verdict
from exhibit_a.operations.policy import annotate_suite_gap, semantic_precision, should_trigger


def test_webhook_policy_is_explicit_and_never_runs_on_synchronize():
    assert should_trigger("issue_comment", comment="/exhibit-a review")
    assert should_trigger("issue_comment", comment="Context\n/exhibit-a review\n")
    assert should_trigger("pull_request", action="ready_for_review")
    assert not should_trigger("pull_request", action="synchronize")
    assert not should_trigger("push")
    assert not should_trigger("issue_comment", comment="please review this")


def test_suite_gap_uses_external_ci_signal_without_changing_verdict():
    case = Case(id="case", mode=Mode.PROSECUTOR, verdict=Verdict.PROVEN)

    annotate_suite_gap(case, existing_suite_passed=True)

    assert case.verdict is Verdict.PROVEN
    assert case.existing_suite_passed is True
    assert case.suite_gap is True


def test_suite_gap_is_false_when_existing_tests_already_fail():
    case = Case(id="case", mode=Mode.PROSECUTOR, verdict=Verdict.PROVEN)

    annotate_suite_gap(case, existing_suite_passed=False)

    assert case.suite_gap is False


def test_semantic_precision_requires_human_labels():
    report = semantic_precision([True, False, True])
    empty = semantic_precision([])

    assert report.human_judged_flags == 3
    assert report.confirmed_regressions == 2
    assert report.precision == 2 / 3
    assert empty.precision is None
