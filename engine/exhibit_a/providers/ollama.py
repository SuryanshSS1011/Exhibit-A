"""Ollama adapter using its loopback OpenAI-compatible chat endpoint."""

from __future__ import annotations

import json
import math
import os
import stat
import time
import urllib.error
import urllib.request
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
_MAX_CONTEXT_ENTRIES = 10_000
_MAX_TIMEOUT_S = 600
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
        _validate_limit(timeout_s, "timeout", _MAX_TIMEOUT_S, allow_float=True)
        _validate_limit(max_response_bytes, "response limit", _MAX_RESPONSE_BYTES)
        _validate_limit(max_context_bytes, "context limit", _MAX_CONTEXT_BYTES)
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
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _repository_context(repo_path: Path, max_bytes: int) -> str:
    root = repo_path.absolute()
    root_descriptor = _open_directory_no_follow(root)
    sections: list[str] = []
    used = 0
    file_count = 0
    try:
        for relative in _context_paths(root_descriptor):
            if file_count >= _MAX_CONTEXT_FILES or used >= max_bytes:
                break
            header = f"\n--- {relative.as_posix()} ---\n".encode()
            remaining = max_bytes - used - len(header)
            if remaining <= 0:
                break
            try:
                content = _read_context_file(root_descriptor, relative, remaining)
            except (OSError, ValueError):
                continue
            section = header.decode() + content.decode("utf-8", errors="ignore")
            sections.append(section)
            used += len(section.encode())
            file_count += 1
    finally:
        os.close(root_descriptor)
    return "".join(sections) or "[no supported repository files found]"


def _open_directory_no_follow(root: Path) -> int:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if no_follow is None or directory is None:
        raise RuntimeError("provider context collection requires O_NOFOLLOW and O_DIRECTORY")
    current = os.open(os.sep, os.O_RDONLY | directory)
    try:
        for part in root.parts[1:]:
            following = os.open(
                part,
                os.O_RDONLY | directory | no_follow,
                dir_fd=current,
            )
            os.close(current)
            current = following
    except OSError as exc:
        os.close(current)
        raise ValueError(f"repo checkout could not be opened safely: {root}") from exc
    return current


def _context_paths(root_descriptor: int) -> list[Path]:
    paths: list[Path] = []
    visited = 0

    def walk(directory_descriptor: int, parent: Path) -> None:
        nonlocal visited
        with os.scandir(directory_descriptor) as entries:
            for entry in entries:
                visited += 1
                if visited > _MAX_CONTEXT_ENTRIES:
                    raise ValueError("provider context contains too many entries")
                if entry.name in _IGNORED_DIRECTORIES or entry.name.startswith("."):
                    continue
                relative = parent / entry.name
                if entry.is_dir(follow_symlinks=False):
                    try:
                        child = os.open(
                            entry.name,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory_descriptor,
                        )
                    except OSError:
                        continue
                    try:
                        walk(child, relative)
                    finally:
                        os.close(child)
                elif entry.is_file(follow_symlinks=False) and (
                    relative.suffix in _CONTEXT_SUFFIXES or entry.name in _CONTEXT_NAMES
                ):
                    paths.append(relative)

    walk(root_descriptor, Path())
    return sorted(
        paths,
        key=lambda path: (path.suffix not in {".py", ".pyi"}, path.as_posix()),
    )


def _read_context_file(root_descriptor: int, relative: Path, limit: int) -> bytes:
    parent = os.dup(root_descriptor)
    try:
        for part in relative.parts[:-1]:
            following = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent,
            )
            os.close(parent)
            parent = following
        descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError(f"provider context is not a private regular file: {relative}")
        content = source.read(limit)
        after = os.fstat(source.fileno())
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(content) != min(after.st_size, limit)
    ):
        raise ValueError(f"provider context changed while it was read: {relative}")
    return content


def _validate_limit(value: object, label: str, maximum: int, *, allow_float: bool = False) -> None:
    expected = (int, float) if allow_float else (int,)
    if (
        not isinstance(value, expected)
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value <= 0
        or value > maximum
    ):
        raise ValueError(f"Ollama {label} must be positive and at most {maximum}")


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
