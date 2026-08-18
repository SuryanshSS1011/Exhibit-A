from __future__ import annotations

import json
from pathlib import Path

from exhibit_a.passport import verify_passport
from exhibit_a.passport_html import render_html_passport

DEMO_KEY = b"exhibit-a-public-dogfood-demo-key-v1!"
BUGGY_SHA = "3c3ec8996383750423f6f32d398850cd7af889e5"
FIXED_SHA = "1f9473f8d6940935ec45a41cb518d9038e0bea0e"
EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "dogfood" / "timeout_false_verified"


def test_checked_in_historical_passport_is_signed_sanitized_and_in_sync():
    json_path = EXAMPLE / "timeout_false_verified.passport.json"
    html_path = EXAMPLE / "timeout_false_verified.passport.html"
    payload = json.loads(json_path.read_text())
    rendered = html_path.read_text()

    assert verify_passport(payload, signing_key=DEMO_KEY)
    assert payload["claim_type"] == "bug_flip"
    assert payload["subject"]["verdict"] == "VERIFIED"
    assert payload["subject"]["deterministic"] is True
    assert payload["subject"]["reruns"] == 3
    assert payload["subject"]["revisions"] == {
        "base_commit": BUGGY_SHA,
        "target_commit": FIXED_SHA,
    }
    assert render_html_passport(payload) == rendered

    public_artifacts = json_path.read_text() + rendered
    for private_value in (
        "/Users/",
        "/private/",
        "AssertionError",
        "TIMEOUT",
        "test_timeout_false_verified.py",
        DEMO_KEY.decode(),
    ):
        assert private_value not in public_artifacts
    assert "<script" not in rendered
    assert "https://" not in rendered
