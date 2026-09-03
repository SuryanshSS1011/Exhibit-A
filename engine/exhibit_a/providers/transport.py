"""Bounded retry for provider HTTP calls.

A proposal is one leg of a longer investigation, and providers rate-limit. Failing a run
on a 429 that would have succeeded a second later throws away the executions already
spent on it. Retries are therefore bounded, apply only to statuses a server can recover
from, and never re-send after a status the server will reject again. A provider still
proposes only; retrying changes how hard the engine tries to ask, never what is admitted.
"""

from __future__ import annotations

import random
import time
import urllib.error
from typing import Callable

# 408 request timeout, 409 conflict, 429 rate limit, and the 5xx family a server may
# recover from. 529 is Anthropic's documented overload status.
RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
MAX_ATTEMPTS = 3
_BASE_DELAY_S = 1.0
_MAX_DELAY_S = 30.0


def request_with_retry(
    send: Callable[[], bytes],
    *,
    status_label: str,
    failure_label: str,
    max_attempts: int = MAX_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> bytes:
    """Call ``send`` until it succeeds, the error is permanent, or attempts run out."""
    if max_attempts < 1:
        raise ValueError("provider retry needs at least one attempt")
    for attempt in range(1, max_attempts + 1):
        try:
            return send()
        except urllib.error.HTTPError as exc:  # a subclass of URLError; must come first
            if exc.code not in RETRYABLE_STATUS or attempt == max_attempts:
                raise RuntimeError(f"{status_label} returned HTTP {exc.code}") from exc
            delay = _delay(attempt, exc, jitter)
        except urllib.error.URLError as exc:
            if attempt == max_attempts:
                raise RuntimeError(f"{failure_label} request failed: {exc.reason}") from exc
            delay = _delay(attempt, None, jitter)
        sleep(delay)
    raise AssertionError("unreachable: the final attempt either returns or raises")


def _delay(
    attempt: int, error: urllib.error.HTTPError | None, jitter: Callable[[], float]
) -> float:
    honored = _retry_after(error)
    if honored is not None:
        return honored
    backoff = min(_BASE_DELAY_S * (2 ** (attempt - 1)), _MAX_DELAY_S)
    # Half the backoff plus jitter, so concurrent runs do not retry in lockstep.
    return backoff * (0.5 + 0.5 * jitter())


def _retry_after(error: urllib.error.HTTPError | None) -> float | None:
    """Honor a numeric Retry-After. The HTTP-date form falls back to backoff."""
    if error is None or error.headers is None:
        return None
    value = error.headers.get("Retry-After")
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except (AttributeError, ValueError):
        return None
    return min(max(seconds, 0.0), _MAX_DELAY_S)
