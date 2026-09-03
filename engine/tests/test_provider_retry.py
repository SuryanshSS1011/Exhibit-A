"""Providers may retry a transient refusal; they may not retry a decided one.

A proposal is one leg of a longer investigation, so losing a run to a 429 discards the
executions already spent on it. Retrying is about how hard the engine asks, never about
what the judge admits -- a retried proposal still has to clear the same gates.
"""

from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from pathlib import Path
from urllib.request import Request

import pytest

from exhibit_a.providers import AnthropicProvider, ProviderRequest
from exhibit_a.providers.transport import MAX_ATTEMPTS, request_with_retry

LABELS = {"status_label": "Provider", "failure_label": "Provider"}


def _http_error(code: int, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.invalid", code, "boom", headers or {}, None)


def _recorder() -> tuple[list[float], object]:
    slept: list[float] = []
    return slept, slept.append


def test_a_transient_status_is_retried_until_it_succeeds():
    slept, sleep = _recorder()
    attempts = []

    def send() -> bytes:
        attempts.append(len(attempts))
        if len(attempts) < 3:
            raise _http_error(429)
        return b"ok"

    assert request_with_retry(send, **LABELS, sleep=sleep, jitter=lambda: 0.0) == b"ok"
    assert len(attempts) == 3
    assert len(slept) == 2


@pytest.mark.parametrize("code", [400, 401, 403, 404, 302, 422])
def test_a_decided_status_is_never_retried(code: int):
    slept, sleep = _recorder()
    attempts = []

    def send() -> bytes:
        attempts.append(code)
        raise _http_error(code)

    with pytest.raises(RuntimeError, match=f"returned HTTP {code}"):
        request_with_retry(send, **LABELS, sleep=sleep, jitter=lambda: 0.0)

    assert attempts == [code], "a status the server already decided must not be re-sent"
    assert slept == []


def test_retries_are_bounded_and_surface_the_last_status():
    slept, sleep = _recorder()
    attempts = []

    def send() -> bytes:
        attempts.append(503)
        raise _http_error(503)

    with pytest.raises(RuntimeError, match="returned HTTP 503"):
        request_with_retry(send, **LABELS, sleep=sleep, jitter=lambda: 0.0)

    assert len(attempts) == MAX_ATTEMPTS


def test_a_connection_failure_is_retried_then_reported():
    slept, sleep = _recorder()
    attempts = []

    def send() -> bytes:
        attempts.append(0)
        raise urllib.error.URLError("connection refused")

    with pytest.raises(RuntimeError, match="request failed: connection refused"):
        request_with_retry(send, **LABELS, sleep=sleep, jitter=lambda: 0.0)

    assert len(attempts) == MAX_ATTEMPTS


def test_a_numeric_retry_after_is_honored_over_backoff():
    slept, sleep = _recorder()
    calls = []

    def send() -> bytes:
        calls.append(0)
        if len(calls) == 1:
            raise _http_error(429, {"Retry-After": "7"})
        return b"ok"

    assert request_with_retry(send, **LABELS, sleep=sleep, jitter=lambda: 0.0) == b"ok"
    assert slept == [7.0]


def test_an_absurd_retry_after_is_clamped():
    slept, sleep = _recorder()
    calls = []

    def send() -> bytes:
        calls.append(0)
        if len(calls) == 1:
            raise _http_error(429, {"Retry-After": "99999"})
        return b"ok"

    request_with_retry(send, **LABELS, sleep=sleep, jitter=lambda: 0.0)
    assert slept and slept[0] <= 30.0


def test_a_date_form_retry_after_falls_back_to_backoff():
    slept, sleep = _recorder()
    calls = []

    def send() -> bytes:
        calls.append(0)
        if len(calls) == 1:
            raise _http_error(429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
        return b"ok"

    request_with_retry(send, **LABELS, sleep=sleep, jitter=lambda: 0.0)
    assert slept == [0.5]


def test_backoff_grows_and_is_jittered():
    slept, sleep = _recorder()

    def send() -> bytes:
        raise _http_error(500)

    with pytest.raises(RuntimeError):
        request_with_retry(send, **LABELS, sleep=sleep, jitter=lambda: 1.0)

    assert slept == [1.0, 2.0]

    low, sleep_low = _recorder()
    with pytest.raises(RuntimeError):
        request_with_retry(send, **LABELS, sleep=sleep_low, jitter=lambda: 0.0)
    assert low == [0.5, 1.0]


def test_a_single_attempt_disables_retrying():
    slept, sleep = _recorder()
    attempts = []

    def send() -> bytes:
        attempts.append(0)
        raise _http_error(429)

    with pytest.raises(RuntimeError, match="returned HTTP 429"):
        request_with_retry(send, **LABELS, max_attempts=1, sleep=sleep)

    assert attempts == [0]
    assert slept == []


class _FlakyOpener:
    """Refuses once with a 429, then serves a valid Anthropic payload."""

    def __init__(self) -> None:
        self.calls = 0

    def open(self, request: Request, *, timeout: float):
        self.calls += 1
        if self.calls == 1:
            raise _http_error(429, {"Retry-After": "0"})
        payload = {
            "model": "claude-served-version",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": '{"candidate": null}'}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        return _FakeResponse(json.dumps(payload).encode())


class _FakeResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def test_a_provider_survives_one_rate_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    provider = AnthropicProvider(model="claude-opus-5")
    opener = _FlakyOpener()
    monkeypatch.setattr(provider, "_opener", opener)

    response = provider.generate(
        ProviderRequest(
            prompt="return a candidate",
            response_schema={"type": "object", "required": ["candidate"]},
            repo_path=tmp_path,
        )
    )

    assert opener.calls == 2
    assert response.output == {"candidate": None}


def test_providers_reject_an_unbounded_retry_budget():
    with pytest.raises(ValueError, match="retry attempts are out of range"):
        AnthropicProvider(model="claude-opus-5", max_attempts=MAX_ATTEMPTS + 1)
    with pytest.raises(ValueError, match="retry attempts are out of range"):
        AnthropicProvider(model="claude-opus-5", max_attempts=0)
