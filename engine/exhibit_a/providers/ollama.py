"""Ollama adapter using its loopback OpenAI-compatible chat endpoint."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .base import (
    ProviderRequest,
    ProviderResponse,
    RuntimeModel,
    TokenUsage,
    UnknownModelIdentity,
)

_DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
_MAX_RESPONSE_BYTES = 2_000_000
_MAX_CONTEXT_BYTES = 200_000
_MAX_CONTEXT_FILES = 200
_CONTEXT_SUFFIXES = {".py", ".pyi", ".toml", ".txt", ".md"}
_CONTEXT_NAMES = {"Dockerfile", "requirements.in", "requirements.txt"}
_IGNORED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _chat_completions_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Ollama base URL must use http or https")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Ollama base URL must not contain credentials, query, or fragment")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Ollama base URL requires a hostname")
    try:
        is_loopback = ip_address(hostname).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise ValueError("Ollama base URL must use a numeric loopback address")
    path = f"{parsed.path.rstrip('/')}/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class OllamaProvider:
    """Generate structured proposals through a local Ollama server.

    The adapter sends no tools and never executes tool calls returned by the server.
    It accepts only an explicit loopback URL and disables HTTP redirects so a local
    endpoint cannot redirect repository context to another host.
    """

    def __init__(
        self,
        *,
        model: str,
        base_url: str = _DEFAULT_BASE_URL,
        timeout_s: float = 120,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
        max_context_bytes: int = _MAX_CONTEXT_BYTES,
    ):
        if not model.strip():
            raise ValueError("Ollama model must not be empty")
        if timeout_s <= 0:
            raise ValueError("Ollama timeout must be positive")
        if max_response_bytes <= 0:
            raise ValueError("Ollama response limit must be positive")
        if max_context_bytes <= 0:
            raise ValueError("Ollama context limit must be positive")
        self.model = model
        self.url = _chat_completions_url(base_url)
        self.timeout_s = timeout_s
        self.max_response_bytes = max_response_bytes
        self.max_context_bytes = max_context_bytes
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )

    def generate(self, request: ProviderRequest) -> ProviderResponse:
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
        http_request = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        started = time.monotonic()
        try:
            with self._opener.open(http_request, timeout=self.timeout_s) as response:
                raw = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Ollama returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ollama request failed: {exc.reason}") from exc
        if len(raw) > self.max_response_bytes:
            raise ValueError("Ollama response exceeded the configured size limit")

        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TypeError("Ollama response was not a JSON object")
        choices = payload.get("choices")
        if (
            isinstance(choices, list)
            and choices
            and isinstance(choices[0], dict)
            and choices[0].get("finish_reason") == "length"
        ):
            raise ValueError("Ollama response was truncated before completion")
        message = _response_message(payload)
        content = message.get("content")
        if not isinstance(content, str):
            raise TypeError("Ollama response did not contain structured text output")
        output = json.loads(content)
        if not isinstance(output, dict):
            raise TypeError("Ollama structured output was not a JSON object")
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
                provider="ollama",
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


def _response_message(payload: dict) -> dict:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("Ollama response did not contain a completion choice")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise TypeError("Ollama response did not contain a completion message")
    return message


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _repository_context(repo_path: Path, max_bytes: int) -> str:
    root = repo_path.resolve()
    if not root.is_dir():
        raise ValueError(f"repo checkout not found: {root}")

    sections: list[str] = []
    used = 0
    file_count = 0
    for path in _context_paths(root):
        if file_count >= _MAX_CONTEXT_FILES or used >= max_bytes:
            break
        relative = path.relative_to(root).as_posix()
        header = f"\n--- {relative} ---\n".encode()
        remaining = max_bytes - used - len(header)
        if remaining <= 0:
            break
        try:
            with path.open("rb") as source:
                content = source.read(remaining)
            text = content.decode("utf-8", errors="ignore")
        except OSError:
            continue
        section = header.decode() + text
        sections.append(section)
        used += len(section.encode())
        file_count += 1
    return "".join(sections) or "[no supported repository files found]"


def _context_paths(root: Path) -> Iterator[Path]:
    for python_only in (True, False):
        for directory, directory_names, file_names in os.walk(root, followlinks=False):
            directory_names[:] = sorted(
                name
                for name in directory_names
                if name not in _IGNORED_DIRECTORIES
                and not name.startswith(".")
                and not (Path(directory) / name).is_symlink()
            )
            for name in sorted(file_names):
                path = Path(directory) / name
                is_python = path.suffix in {".py", ".pyi"}
                is_supported = path.suffix in _CONTEXT_SUFFIXES or name in _CONTEXT_NAMES
                if (
                    path.is_symlink()
                    or not is_supported
                    or (python_only and not is_python)
                    or (not python_only and is_python)
                ):
                    continue
                yield path


def _validate_schema(value: object, schema: dict, path: str = "$") -> None:
    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        for option in any_of:
            if not isinstance(option, dict):
                continue
            try:
                _validate_schema(value, option, path)
                return
            except (TypeError, ValueError):
                pass
        raise ValueError(f"Ollama structured output did not match any schema at {path}")

    expected_type = schema.get("type")
    expected_types = expected_type if isinstance(expected_type, list) else [expected_type]
    if expected_type is not None and not any(
        _matches_json_type(value, item) for item in expected_types if isinstance(item, str)
    ):
        raise TypeError(f"Ollama structured output had the wrong type at {path}")

    allowed = schema.get("enum")
    if isinstance(allowed, list) and value not in allowed:
        raise ValueError(f"Ollama structured output had an invalid enum value at {path}")

    if isinstance(value, dict):
        required = schema.get("required")
        if isinstance(required, list):
            missing = [key for key in required if key not in value]
            if missing:
                raise ValueError(f"Ollama structured output was missing {missing!r} at {path}")
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        if schema.get("additionalProperties") is False:
            unexpected = [key for key in value if key not in properties]
            if unexpected:
                raise ValueError(
                    f"Ollama structured output had unexpected keys {unexpected!r} at {path}"
                )
        for key, item in value.items():
            item_schema = properties.get(key)
            if isinstance(item_schema, dict):
                _validate_schema(item, item_schema, f"{path}.{key}")

    if isinstance(value, list):
        maximum = schema.get("maxItems")
        if isinstance(maximum, int) and len(value) > maximum:
            raise ValueError(f"Ollama structured output exceeded maxItems at {path}")
        if schema.get("uniqueItems") is True:
            encoded = [json.dumps(item, sort_keys=True) for item in value]
            if len(encoded) != len(set(encoded)):
                raise ValueError(f"Ollama structured output had duplicate items at {path}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_schema(item, item_schema, f"{path}[{index}]")


def _matches_json_type(value: object, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False
