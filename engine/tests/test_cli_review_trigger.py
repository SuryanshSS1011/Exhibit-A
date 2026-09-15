"""Deciding whether a webhook event earns a review.

A multi-minute, model-backed investigation is not viable on every pushed commit, and a
workflow that restated the policy in YAML would be a second policy free to drift from the
first. The boundary therefore asks the same function the library does.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from exhibit_a import cli


def _event(tmp_path: Path, payload: dict) -> str:
    path = tmp_path / "event.json"
    path.write_text(json.dumps(payload))
    return str(path)


def _decide(
    capsys: pytest.CaptureFixture[str], event_name: str, path: str | None
) -> tuple[int, str]:
    argv = ["review-trigger", "--event-name", event_name]
    if path is not None:
        argv += ["--event-path", path]
    code = cli.main(argv)
    return code, capsys.readouterr().out.strip()


def test_a_pull_request_becoming_ready_earns_a_review(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    code, out = _decide(capsys, "pull_request", _event(tmp_path, {"action": "ready_for_review"}))

    assert (code, out) == (0, "review")


@pytest.mark.parametrize("action", ["synchronize", "opened", "edited", "labeled"])
def test_ordinary_pull_request_activity_does_not(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, action: str
):
    # Every pushed commit would otherwise start a multi-minute model-backed run.
    code, out = _decide(capsys, "pull_request", _event(tmp_path, {"action": action}))

    assert (code, out) == (1, "skip")


def test_the_explicit_review_command_earns_one(capsys: pytest.CaptureFixture[str], tmp_path: Path):
    code, out = _decide(
        capsys,
        "issue_comment",
        _event(tmp_path, {"action": "created", "comment": {"body": "/exhibit-a review"}}),
    )

    assert (code, out) == (0, "review")


@pytest.mark.parametrize(
    "body",
    ["looks good to me", "should we /exhibit-a review this?", "", "/exhibit-a"],
)
def test_an_ordinary_comment_does_not(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, body: str
):
    code, out = _decide(
        capsys,
        "issue_comment",
        _event(tmp_path, {"action": "created", "comment": {"body": body}}),
    )

    assert (code, out) == (1, "skip")


def test_an_unrelated_event_does_not(capsys: pytest.CaptureFixture[str], tmp_path: Path):
    for event in ("push", "workflow_dispatch", "schedule"):
        code, out = _decide(capsys, event, _event(tmp_path, {"action": "created"}))
        assert (code, out) == (1, "skip"), event


def test_a_missing_or_malformed_payload_is_an_error_not_a_review(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    # Failing open here would run a review on any event whose payload we could not read.
    missing = str(tmp_path / "absent.json")
    assert (
        cli.main(["review-trigger", "--event-name", "pull_request", "--event-path", missing]) == 2
    )

    not_an_object = tmp_path / "list.json"
    not_an_object.write_text("[1, 2]")
    assert (
        cli.main(
            ["review-trigger", "--event-name", "pull_request", "--event-path", str(not_an_object)]
        )
        == 2
    )


def test_a_comment_payload_that_is_not_an_object_is_ignored_safely(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    code, out = _decide(
        capsys,
        "issue_comment",
        _event(tmp_path, {"action": "created", "comment": "a string, not an object"}),
    )

    assert (code, out) == (1, "skip")
