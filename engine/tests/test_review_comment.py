"""A Case becomes a pull-request comment, or it becomes nothing.

The product's rule is a runnable fail-to-pass test or silence. A comment is where that
rule is kept or quietly broken, so the negative cases here matter as much as the
positive one: a reviewer that posts "no issues found" on every pull request is the alert
fatigue this project exists to avoid, wearing a politer face.
"""

from __future__ import annotations

import pytest

from exhibit_a.models.case import Case, Evidence, Mode, Verdict
from exhibit_a.models.case import TestArtifact as CaseTestArtifact
from exhibit_a.operations.review_comment import render_review_comment


def _proven(**updates: object) -> Case:
    case = Case(
        id="c1",
        mode=Mode.PROSECUTOR,
        verdict=Verdict.VERIFIED,
        claim_text="refactor the stock lookup",
        run_command="python3 -m pytest -x -q test_repro.py",
        base_commit="b" * 40,
        target_commit="a" * 40,
        root_cause_narrative="the lookup stopped defaulting to zero",
        test_file=CaseTestArtifact(
            path="test_repro.py",
            code="from inventory import stock_for\n\n\ndef test_missing():\n    assert stock_for({}, 'x') == 0\n",
        ),
        evidence=Evidence(
            fail_log="E   KeyError: 'x'\ntest_repro.py:5: KeyError",
            fail_signature="KeyError: 'x'",
            pass_log="1 passed in 0.01s",
            reruns=5,
            deterministic=True,
        ),
    )
    for key, value in updates.items():
        setattr(case, key, value)
    return case


def test_a_proven_flip_earns_a_comment_carrying_the_evidence():
    comment = render_review_comment(_proven())

    assert comment is not None
    # The three things a reader needs to check the claim themselves.
    assert "test_repro.py" in comment
    assert "python3 -m pytest -x -q test_repro.py" in comment
    assert "KeyError" in comment
    assert "1 passed" in comment
    assert "5 of 5" in comment and "deterministic" in comment


@pytest.mark.parametrize("verdict", [Verdict.UNCERTAIN, Verdict.PARTIAL, Verdict.FAILED])
def test_anything_short_of_a_proven_flip_renders_nothing(verdict: Verdict):
    # Not an empty comment, not a "nothing found" comment. Nothing.
    assert render_review_comment(_proven(verdict=verdict)) is None


def test_a_verdict_without_its_artefacts_renders_nothing():
    # A VERIFIED Case that cannot show its test or its failing run cannot support a
    # comment, whatever its verdict field says.
    assert render_review_comment(_proven(test_file=None)) is None
    assert render_review_comment(_proven(evidence=Evidence(fail_log=""))) is None


def test_the_model_narrative_is_never_presented_as_the_finding():
    comment = render_review_comment(_proven())

    assert comment is not None
    narrative_at = comment.index("the lookup stopped defaulting to zero")
    assert "which is not the evidence" in comment[:narrative_at]
    # And it sits below the executed evidence, not above it.
    assert comment.index("Reproduce it") < narrative_at


def test_host_paths_never_reach_the_comment():
    # The local executor runs in a scratch copy, so its logs carry the runner's
    # filesystem and its user name.
    case = _proven(
        evidence=Evidence(
            fail_log=(
                "/private/var/folders/qn/xx/T/exhibit-a-9f2/repo/test_repro.py:5: KeyError\n"
                "/Users/someone/checkouts/thing/inventory.py:12: in stock_for"
            ),
            fail_signature="KeyError: 'x'",
            pass_log="1 passed",
            reruns=5,
            deterministic=True,
        )
    )

    comment = render_review_comment(case)

    assert comment is not None
    for leak in ("/Users/", "/home/", "/var/folders", "/private/var", "exhibit-a-9f2"):
        assert leak not in comment, leak
    # The useful part of the path survives the scrub.
    assert "test_repro.py:5" in comment


def test_container_paths_survive_because_they_are_not_host_paths():
    case = _proven(
        evidence=Evidence(
            fail_log="/work/test_repro.py:5: KeyError",
            fail_signature="KeyError",
            pass_log="1 passed",
            reruns=1,
            deterministic=True,
        )
    )

    comment = render_review_comment(case)

    assert comment is not None and "/work/test_repro.py:5" in comment


def test_credential_shaped_environment_values_are_redacted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SOME_API_KEY", "sk-live-abcdefghijklmnop")
    case = _proven(
        evidence=Evidence(
            fail_log="requests.post(headers={'x': 'sk-live-abcdefghijklmnop'})",
            fail_signature="KeyError",
            pass_log="1 passed",
            reruns=1,
            deterministic=True,
        )
    )

    comment = render_review_comment(case)

    assert comment is not None
    assert "sk-live-abcdefghijklmnop" not in comment
    assert "[redacted]" in comment


def test_a_long_log_keeps_its_tail_where_the_failure_is():
    case = _proven(
        evidence=Evidence(
            fail_log="noise\n" * 5000 + "E   KeyError: 'x'",
            fail_signature="KeyError: 'x'",
            pass_log="1 passed",
            reruns=1,
            deterministic=True,
        )
    )

    comment = render_review_comment(case)

    assert comment is not None
    assert "E   KeyError: 'x'" in comment
    assert "[...]" in comment
    assert len(comment) < 20000


def test_execution_output_cannot_break_out_of_its_fence():
    """Logs are attacker-influenced, and a fence inside one ends ours early.

    Everything after that renders as Markdown or HTML in the pull request, which turns a
    log line into arbitrary comment content.
    """
    case = _proven(
        evidence=Evidence(
            fail_log="```\n</details><img src=x onerror=alert(1)>\n@everyone\n```",
            fail_signature="KeyError",
            pass_log="1 passed",
            reruns=1,
            deterministic=True,
        )
    )

    comment = render_review_comment(case)

    assert comment is not None
    # Our fence is longer than any run of backticks inside the body, so the body stays
    # inside it rather than closing it.
    for line in comment.splitlines():
        if "onerror" in line:
            break
    else:
        raise AssertionError("the log was dropped rather than fenced")
    assert "````" in comment


def test_untrusted_claim_text_is_fenced_too():
    # The claim is a pull request title, which anyone opening a pull request controls.
    case = _proven(claim_text="nice change\n</details>\n\n# Injected heading")

    comment = render_review_comment(case)

    assert comment is not None
    heading_at = comment.index("# Injected heading")
    fence_before = comment.rindex("```", 0, heading_at)
    assert fence_before > comment.index("Change under review")


def test_more_host_path_shapes_are_removed():
    case = _proven(
        evidence=Evidence(
            fail_log="/root/.ssh/id_rsa\nC:\\\\Users\\\\runneradmin\\\\secrets.txt",
            fail_signature="KeyError",
            pass_log="1 passed",
            reruns=1,
            deterministic=True,
        )
    )

    comment = render_review_comment(case)

    assert comment is not None
    assert "/root/" not in comment
    assert "runneradmin" not in comment
