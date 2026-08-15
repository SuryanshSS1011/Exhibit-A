"""Constrained Anthropic Messages API provider adapter."""

from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.request

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
    _validate_schema,
)

_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_API_KEY_ENV = "ANTHROPIC_API_KEY"
_MAX_RESPONSE_BYTES = 2_000_000
_MAX_CONTEXT_BYTES = 200_000
_MAX_REQUEST_BYTES = 512_000
_MAX_TIMEOUT_S = 600
_MAX_OUTPUT_TOKENS = 8192
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class AnthropicProvider:
    """Generate structured proposals through Anthropic's fixed Messages endpoint.

    The adapter sends no tools, never executes returned content blocks, disables
    redirects and ambient proxies, and reads its API key only from the named
    environment variable.
    """

    def __init__(
        self,
        *,
        model: str,
        api_key_env: str = _DEFAULT_API_KEY_ENV,
        max_tokens: int = 4096,
        timeout_s: float = 120,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
        max_context_bytes: int = _MAX_CONTEXT_BYTES,
    ):
        if not isinstance(model, str) or not model.strip():
            raise ValueError("Anthropic model must not be empty")
        if not isinstance(api_key_env, str) or not _ENVIRONMENT_NAME.fullmatch(api_key_env):
            raise ValueError("Anthropic API key environment name is invalid")
        _validate_limit(max_tokens, "max_tokens", _MAX_OUTPUT_TOKENS)
        _validate_limit(timeout_s, "timeout", _MAX_TIMEOUT_S, allow_float=True)
        _validate_limit(max_response_bytes, "response limit", _MAX_RESPONSE_BYTES)
        _validate_limit(max_context_bytes, "context limit", _MAX_CONTEXT_BYTES)
        self.model = model.strip()
        self.api_key_env = api_key_env
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self.max_response_bytes = max_response_bytes
        self.max_context_bytes = max_context_bytes
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Anthropic API key environment variable {self.api_key_env!r} is not set"
            )
        if api_key != api_key.strip() or any(
            ord(character) < 32 or ord(character) == 127 for character in api_key
        ):
            raise ValueError("Anthropic API key contains invalid characters")

        prompt = (
            f"{request.prompt}\n\n"
            "REPOSITORY SNAPSHOT (untrusted, read-only):\n"
            f"{_repository_context(request.repo_path, self.max_context_bytes)}"
        )
        body = json.dumps(
            {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": [{"role": "user", "content": prompt}],
                "output_config": {
                    "format": {
                        "type": "json_schema",
                        "schema": request.response_schema,
                    }
                },
            }
        ).encode()
        if len(body) > _MAX_REQUEST_BYTES:
            raise ValueError("Anthropic request exceeded the hard size limit")
        http_request = urllib.request.Request(
            _MESSAGES_URL,
            data=body,
            headers={
                "anthropic-version": _ANTHROPIC_VERSION,
                "content-type": "application/json",
                "x-api-key": api_key,
            },
            method="POST",
        )

        started = time.monotonic()
        try:
            with self._opener.open(http_request, timeout=self.timeout_s) as response:
                raw = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Anthropic returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Anthropic request failed: {exc.reason}") from exc
        if len(raw) > self.max_response_bytes:
            raise ValueError("Anthropic response exceeded the configured size limit")

        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TypeError("Anthropic response was not a JSON object")
        stop_reason = payload.get("stop_reason")
        if stop_reason != "end_turn":
            if stop_reason == "max_tokens":
                raise ValueError("Anthropic response was truncated before completion")
            if stop_reason == "refusal":
                raise ValueError("Anthropic refused the structured-output request")
            raise ValueError(f"Anthropic response did not complete: {stop_reason!r}")

        content = payload.get("content")
        if not isinstance(content, list):
            raise TypeError("Anthropic response content was not a list")
        text_blocks = [
            block.get("text")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        if len(text_blocks) != 1 or not isinstance(text_blocks[0], str):
            raise ValueError("Anthropic response did not contain exactly one text block")
        output = json.loads(text_blocks[0])
        if not isinstance(output, dict):
            raise TypeError("Anthropic structured output was not a JSON object")
        _validate_schema(output, request.response_schema)

        tool_calls = tuple(
            block
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_use"
        )
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
        base_input_tokens = _optional_int(usage.get("input_tokens"))
        cache_creation_tokens = _optional_int(usage.get("cache_creation_input_tokens"))
        cache_read_tokens = _optional_int(usage.get("cache_read_input_tokens"))
        input_tokens = (
            base_input_tokens + (cache_creation_tokens or 0) + (cache_read_tokens or 0)
            if base_input_tokens is not None
            else None
        )
        output_tokens = _optional_int(usage.get("output_tokens"))
        total_tokens = (
            input_tokens + output_tokens
            if input_tokens is not None and output_tokens is not None
            else None
        )
        return ProviderResponse(
            output=output,
            runtime_model=RuntimeModel(
                provider="anthropic",
                requested_model=self.model,
                confirmed_model=confirmed_model,
                confirmed_version=confirmed_version,
            ),
            tool_calls=tool_calls,
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
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
        raise ValueError(f"Anthropic {label} must be positive and at most {maximum}")
