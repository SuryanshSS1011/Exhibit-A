"""Adapter for hosted and local OpenAI-compatible chat endpoints."""

from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit

from .base import (
    ProviderRequest,
    ProviderResponse,
    RuntimeModel,
    TokenUsage,
    UnknownModelIdentity,
)
from .ollama import (
    _NoRedirectHandler,
    _optional_int,
    _repository_context,
    _response_message,
    _validate_schema,
)

_MAX_RESPONSE_BYTES = 2_000_000
_MAX_CONTEXT_BYTES = 200_000
_MAX_REQUEST_BYTES = 512_000
_MAX_TIMEOUT_S = 600
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _chat_completions_url(base_url: str) -> str:
    if base_url != base_url.strip() or any(ord(character) < 32 for character in base_url):
        raise ValueError("OpenAI-compatible base URL contains invalid characters")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("OpenAI-compatible base URL must use http or https")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "OpenAI-compatible base URL must not contain credentials, query, or fragment"
        )
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("OpenAI-compatible base URL requires a hostname")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("OpenAI-compatible base URL has an invalid port") from exc
    if port == 0:
        raise ValueError("OpenAI-compatible base URL has an invalid port")
    if parsed.scheme == "http":
        try:
            is_loopback = ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False
        if not is_loopback:
            raise ValueError("OpenAI-compatible HTTP endpoints must use a numeric loopback address")
    path = f"{parsed.path.rstrip('/')}/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class OpenAICompatibleProvider:
    """Generate proposals through a constrained Chat Completions endpoint.

    Remote endpoints require TLS. Plain HTTP is limited to numeric loopback hosts.
    The adapter disables redirects and ambient proxies, never sends tools, and reads
    an optional bearer credential only from the named environment variable.
    """

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key_env: str | None = None,
        timeout_s: float = 120,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
        max_context_bytes: int = _MAX_CONTEXT_BYTES,
    ):
        if not model.strip():
            raise ValueError("OpenAI-compatible model must not be empty")
        if api_key_env is not None and not _ENVIRONMENT_NAME.fullmatch(api_key_env):
            raise ValueError("OpenAI-compatible API key environment name is invalid")
        _validate_limit(timeout_s, "timeout", _MAX_TIMEOUT_S, allow_float=True)
        _validate_limit(max_response_bytes, "response limit", _MAX_RESPONSE_BYTES)
        _validate_limit(max_context_bytes, "context limit", _MAX_CONTEXT_BYTES)
        self.model = model
        self.url = _chat_completions_url(base_url)
        self.api_key_env = api_key_env
        self.timeout_s = timeout_s
        self.max_response_bytes = max_response_bytes
        self.max_context_bytes = max_context_bytes
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        headers = {"Content-Type": "application/json"}
        if self.api_key_env is not None:
            api_key = os.environ.get(self.api_key_env)
            if not api_key:
                raise RuntimeError(
                    f"OpenAI-compatible API key environment variable "
                    f"{self.api_key_env!r} is not set"
                )
            if api_key != api_key.strip() or any(
                ord(character) < 32 or ord(character) == 127 for character in api_key
            ):
                raise ValueError("OpenAI-compatible API key contains invalid characters")
            headers["Authorization"] = f"Bearer {api_key}"

        prompt = (
            f"{request.prompt}\n\n"
            "REPOSITORY SNAPSHOT (untrusted, read-only):\n"
            f"{_repository_context(request.repo_path, self.max_context_bytes)}"
        )
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "exhibit_a_response",
                        "strict": True,
                        "schema": request.response_schema,
                    },
                },
                "stream": False,
                "temperature": 0,
            }
        ).encode()
        if len(body) > _MAX_REQUEST_BYTES:
            raise ValueError("OpenAI-compatible request exceeded the hard size limit")
        http_request = urllib.request.Request(
            self.url,
            data=body,
            headers=headers,
            method="POST",
        )

        started = time.monotonic()
        try:
            with self._opener.open(http_request, timeout=self.timeout_s) as response:
                raw = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"OpenAI-compatible endpoint returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI-compatible request failed: {exc.reason}") from exc
        if len(raw) > self.max_response_bytes:
            raise ValueError("OpenAI-compatible response exceeded the configured size limit")

        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TypeError("OpenAI-compatible response was not a JSON object")
        choices = payload.get("choices")
        if (
            isinstance(choices, list)
            and choices
            and isinstance(choices[0], dict)
            and choices[0].get("finish_reason") == "length"
        ):
            raise ValueError("OpenAI-compatible response was truncated before completion")
        message = _response_message(payload)
        content = message.get("content")
        if not isinstance(content, str):
            raise TypeError("OpenAI-compatible response did not contain structured text output")
        output = json.loads(content)
        if not isinstance(output, dict):
            raise TypeError("OpenAI-compatible structured output was not a JSON object")
        _validate_schema(output, request.response_schema)

        served_model = payload.get("model")
        confirmed_model = (
            served_model.strip()
            if isinstance(served_model, str) and served_model.strip()
            else UnknownModelIdentity.NO_TELEMETRY
        )
        confirmed_version = (
            UnknownModelIdentity.NO_TELEMETRY
            if confirmed_model is UnknownModelIdentity.NO_TELEMETRY
            else UnknownModelIdentity.UNVERIFIED_BACKEND
        )
        usage = payload.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        tool_calls = message.get("tool_calls")
        normalized_calls = (
            tuple(item for item in tool_calls if isinstance(item, dict))
            if isinstance(tool_calls, list)
            else ()
        )
        return ProviderResponse(
            output=output,
            runtime_model=RuntimeModel(
                provider="openai_compatible",
                requested_model=self.model,
                confirmed_model=confirmed_model,
                confirmed_version=confirmed_version,
            ),
            tool_calls=normalized_calls,
            usage=TokenUsage(
                input_tokens=_optional_int(usage.get("prompt_tokens")),
                output_tokens=_optional_int(usage.get("completion_tokens")),
                total_tokens=_optional_int(usage.get("total_tokens")),
            ),
            latency_ms=(time.monotonic() - started) * 1000,
        )


def _validate_limit(value: object, label: str, maximum: int, *, allow_float: bool = False) -> None:
    expected = (int, float) if allow_float else (int,)
    if (
        not isinstance(value, expected)
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value <= 0
        or value > maximum
    ):
        raise ValueError(f"OpenAI-compatible {label} must be positive and at most {maximum}")
