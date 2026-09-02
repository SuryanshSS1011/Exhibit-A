from __future__ import annotations

import json
from pathlib import Path

from exhibit_a.passport import verify_passport
from exhibit_a.passport_html import render_html_passport
from exhibit_a.release_evidence import connector_output_from_receipt

DEMO_KEY = b"exhibit-a-public-release-dogfood-key-v1"
BUGGY_SHA = "3c3ec8996383750423f6f32d398850cd7af889e5"
RELEASE_SHA = "de669e7e09aa5694911fe524ab30253f75a6b5cc"
OBSERVED_AT = "2026-09-02T21:07:18.553485+00:00"
EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "dogfood" / "exhibit_a_ci"


def test_checked_ci_receipt_is_normalized_complete_and_bound_to_the_release_revision():
    receipt = json.loads((EXAMPLE / "ci_status.receipt.json").read_text())

    output = connector_output_from_receipt(receipt)

    assert output.payload.revision == RELEASE_SHA
    assert output.payload.reported_total == 5
    assert [check.name for check in output.payload.checks] == [
        "build",
        "deploy",
        "engine",
        "report-build-status",
        "web",
    ]
    assert all(
        check.status == "completed" and check.conclusion == "success"
        for check in output.payload.checks
    )
    assert output.provenance.observed_at == OBSERVED_AT
    assert output.provenance.source_revision == RELEASE_SHA
    assert output.provenance.security.source_access == "read_only"
    assert output.provenance.security.credential_access == "none"


def test_checked_release_passport_is_signed_sanitized_and_in_sync():
    json_path = EXAMPLE / "exhibit_a_ci.passport.json"
    html_path = EXAMPLE / "exhibit_a_ci.passport.html"
    payload = json.loads(json_path.read_text())
    rendered = html_path.read_text()

    assert verify_passport(payload, signing_key=DEMO_KEY)
    assert payload["schema_version"] == "exhibit-a-passport/v2"
    assert payload["subject"]["verdict"] == "VERIFIED"
    assert payload["subject"]["truth"] == {
        "execution": "COMPLETED",
        "goal": "VERIFIED",
        "release": "SAFE",
    }
    assert payload["subject"]["revisions"] == {
        "base_commit": BUGGY_SHA,
        "target_commit": RELEASE_SHA,
    }

    release = payload["release_evidence"]
    assert release["release"] == "SAFE"
    assert release["reason"] == "all required CI checks completed successfully: engine, web"
    assert release["freshness"]["observed_at"] == OBSERVED_AT
    assert release["freshness"]["evaluated_at"] == OBSERVED_AT
    assert [check["name"] for check in release["checks"]] == ["engine", "web"]
    assert release["collection"] == {
        "reported_total": 5,
        "collected_count": 5,
        "omitted_check_count": 3,
        "all_check_names_unique": True,
    }
    assert render_html_passport(payload) == rendered

    public_artifacts = json_path.read_text() + rendered
    for private_value in (
        "/Users/",
        "/private/",
        "api.github.com",
        "SuryanshSS1011/Exhibit-A",
        "deploy",
        "report-build-status",
        "AssertionError",
        "TIMEOUT",
        "test_timeout_false_verified.py",
        DEMO_KEY.decode(),
    ):
        assert private_value not in public_artifacts
    assert "It neither proves program correctness nor changes the independently" in rendered
    assert "<script" not in rendered
