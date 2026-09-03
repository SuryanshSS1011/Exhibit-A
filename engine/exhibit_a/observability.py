"""Operational logging for the engine's fallible layers.

Logs describe what the engine *did*: which image it built, which remote it cloned, when a
provider was throttled, what a timeout killed. They never carry a verdict. The
deterministic judge stays pure and records its reasoning in the Case's ``silence_reason``,
which is the artifact a reader is meant to audit — a log line is an operator convenience
and may be sampled, filtered, or switched off entirely.

Nothing here writes to stdout. The CLI's ``--json`` and ``--events`` output is a protocol
the web route parses line by line, so a stray log line on stdout would corrupt it.

Only an entry point calls :func:`configure`; library modules take a logger and nothing else.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sys
from contextlib import contextmanager
from typing import IO, Iterator

LOG_LEVEL_ENV = "EXHIBIT_A_LOG_LEVEL"
LOG_FORMAT_ENV = "EXHIBIT_A_LOG_FORMAT"
_ROOT_LOGGER = "exhibit_a"
_RUN_ID: contextvars.ContextVar[str] = contextvars.ContextVar("exhibit_a_run_id", default="-")
# Values of variables named like these never appear in a log line, whatever a caller or a
# raised exception happens to carry.
_SECRET_NAME = re.compile(r"(?:API_?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.IGNORECASE)
_MIN_SECRET_LENGTH = 12
_REDACTED = "[redacted]"


@contextmanager
def run_context(run_id: str) -> Iterator[None]:
    """Tag every log line emitted in this context with one run's identifier."""
    token = _RUN_ID.set(run_id)
    try:
        yield
    finally:
        _RUN_ID.reset(token)


def current_run_id() -> str:
    return _RUN_ID.get()


class _RunIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _RUN_ID.get()
        return True


class _SecretRedactingFilter(logging.Filter):
    """Scrub secret-shaped environment values from anything about to be written.

    The engine never logs a credential deliberately. This is the net under that: an
    exception carrying a URL or a header should not be the thing that writes a key to disk.
    """

    def __init__(self, secrets: tuple[str, ...]):
        super().__init__()
        self._secrets = secrets

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        rendered = record.getMessage()
        scrubbed = rendered
        for secret in self._secrets:
            scrubbed = scrubbed.replace(secret, _REDACTED)
        if scrubbed != rendered:
            record.msg = scrubbed
            record.args = ()
        return True


def _environment_secrets() -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                value
                for name, value in os.environ.items()
                if _SECRET_NAME.search(name) and len(value) >= _MIN_SECRET_LENGTH
            },
            key=len,
            reverse=True,
        )
    )


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "run_id": getattr(record, "run_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure(
    level: str | int | None = None,
    *,
    stream: IO[str] | None = None,
    json_format: bool | None = None,
) -> logging.Logger:
    """Attach one stderr handler to the package logger. Safe to call more than once."""
    logger = logging.getLogger(_ROOT_LOGGER)
    resolved_level = level if level is not None else os.environ.get(LOG_LEVEL_ENV, "WARNING")
    if isinstance(resolved_level, str):
        resolved_level = logging.getLevelNamesMapping().get(
            resolved_level.strip().upper(), logging.WARNING
        )
    logger.setLevel(resolved_level)
    # Never inherit a root handler that might be pointed at stdout by an embedding host.
    logger.propagate = False
    for existing in list(logger.handlers):
        logger.removeHandler(existing)

    as_json = (
        json_format
        if json_format is not None
        else os.environ.get(LOG_FORMAT_ENV, "").strip().lower() == "json"
    )
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(
        _JsonFormatter()
        if as_json
        else logging.Formatter("%(asctime)s %(levelname)-7s [%(run_id)s] %(name)s: %(message)s")
    )
    handler.addFilter(_RunIdFilter())
    handler.addFilter(_SecretRedactingFilter(_environment_secrets()))
    logger.addHandler(handler)
    return logger
