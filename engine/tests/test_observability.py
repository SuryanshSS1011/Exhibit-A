"""Logs are an operator convenience with two hard rules: not stdout, and not secrets.

The CLI's stdout is a protocol the web route parses line by line, so a log line there
corrupts a run. And a credential must not reach a log file just because an exception
happened to carry one.
"""

from __future__ import annotations

import io
import json
import logging
import urllib.error

import pytest

from exhibit_a import observability
from exhibit_a.providers.transport import request_with_retry


@pytest.fixture(autouse=True)
def restore_logging():
    logger = logging.getLogger("exhibit_a")
    handlers, level, propagate = list(logger.handlers), logger.level, logger.propagate
    yield
    logger.handlers = handlers
    logger.setLevel(level)
    logger.propagate = propagate


def test_configure_writes_to_the_given_stream_only():
    stream = io.StringIO()
    observability.configure("info", stream=stream)

    logging.getLogger("exhibit_a.example").info("hello")

    assert "hello" in stream.getvalue()


def test_the_package_logger_never_propagates_to_a_root_handler():
    """An embedding host may have put a root handler on stdout; that must not capture us."""
    observability.configure("info", stream=io.StringIO())

    assert logging.getLogger("exhibit_a").propagate is False


def test_repeated_configuration_does_not_stack_handlers():
    observability.configure("info", stream=io.StringIO())
    observability.configure("info", stream=io.StringIO())

    assert len(logging.getLogger("exhibit_a").handlers) == 1


def test_run_context_tags_lines_and_restores_the_previous_id():
    stream = io.StringIO()
    observability.configure("info", stream=stream)

    with observability.run_context("abc123"):
        logging.getLogger("exhibit_a.example").info("inside")
        assert observability.current_run_id() == "abc123"

    assert observability.current_run_id() == "-"
    assert "abc123" in stream.getvalue()


def test_secret_shaped_environment_values_are_scrubbed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SOMETHING_API_KEY", "sk-live-abcdefghijklmnop")
    stream = io.StringIO()
    observability.configure("info", stream=stream)

    logging.getLogger("exhibit_a.example").info("call failed for sk-live-abcdefghijklmnop on retry")

    written = stream.getvalue()
    assert "sk-live-abcdefghijklmnop" not in written
    assert "[redacted]" in written


def test_a_short_value_is_not_treated_as_a_secret(monkeypatch: pytest.MonkeyPatch):
    """Redacting a short value would blank unrelated words out of every message."""
    monkeypatch.setenv("SHORT_TOKEN", "abc")
    stream = io.StringIO()
    observability.configure("info", stream=stream)

    logging.getLogger("exhibit_a.example").info("abc appears in prose")

    assert "abc appears in prose" in stream.getvalue()


def test_json_format_emits_one_parseable_object_per_line():
    stream = io.StringIO()
    observability.configure("info", stream=stream, json_format=True)

    with observability.run_context("run-7"):
        logging.getLogger("exhibit_a.example").warning("throttled")

    record = json.loads(stream.getvalue().strip())
    assert record["level"] == "WARNING"
    assert record["run_id"] == "run-7"
    assert record["message"] == "throttled"
    assert record["logger"] == "exhibit_a.example"


def test_level_comes_from_the_environment_when_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(observability.LOG_LEVEL_ENV, "debug")
    stream = io.StringIO()
    observability.configure(stream=stream)

    logging.getLogger("exhibit_a.example").debug("verbose")

    assert "verbose" in stream.getvalue()


def test_an_unknown_level_falls_back_to_warning(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(observability.LOG_LEVEL_ENV, "chatty")
    stream = io.StringIO()
    observability.configure(stream=stream)

    logging.getLogger("exhibit_a.example").info("should not appear")
    logging.getLogger("exhibit_a.example").warning("should appear")

    written = stream.getvalue()
    assert "should not appear" not in written
    assert "should appear" in written


def test_a_throttled_provider_says_so_in_the_log():
    stream = io.StringIO()
    observability.configure("warning", stream=stream)
    calls = []

    def send() -> bytes:
        calls.append(0)
        if len(calls) == 1:
            raise urllib.error.HTTPError("https://x.invalid", 429, "slow down", {}, None)
        return b"ok"

    request_with_retry(
        send,
        status_label="Provider",
        failure_label="Provider",
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )

    written = stream.getvalue()
    assert "HTTP 429" in written and "retrying" in written
