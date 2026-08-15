from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from pathlib import Path
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request

import pytest

from exhibit_a.providers import AnthropicProvider, ProviderRequest, UnknownModelIdentity


class FakeResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class FakeOpener:
    def __init__(self, payload: dict):
        self.payload = payload
        self.request: Request | None = None
        self.timeout: float | None = None

    def open(self, request: Request, *, timeout: float):
        self.request = request
        self.timeout = timeout
        return FakeResponse(json.dumps(self.payload).encode())


def _request(repo_path: Path, *, prompt: str = "return a candidate") -> ProviderRequest:
    return ProviderRequest(
        prompt=prompt,
        response_schema={"type": "object", "required": ["candidate"]},
        repo_path=repo_path,
    )


def _response(**overrides: object) -> dict:
    payload: dict[str, object] = {
        "model": "claude-served-version",
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": '{"candidate": null}'}],
        "usage": {
            "input_tokens": 15,
            "cache_creation_input_tokens": 3,
            "cache_read_input_tokens": 2,
            "output_tokens": 4,
        },
    }
    payload.update(overrides)
    return payload


def test_generate_uses_messages_structured_output_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "inventory.py").write_text("stock = 0\n")
    monkeypatch.setenv("EXHIBIT_A_ANTHROPIC_KEY", "test-secret")
    opener = FakeOpener(_response())
    provider = AnthropicProvider(
        model="claude-requested",
        api_key_env="EXHIBIT_A_ANTHROPIC_KEY",
        max_tokens=1024,
        timeout_s=8,
    )
    provider._opener = opener

    response = provider.generate(_request(tmp_path))

    assert response.output == {"candidate": None}
    assert response.runtime_model.provider == "anthropic"
    assert response.runtime_model.requested_model == "claude-requested"
    assert response.runtime_model.confirmed_model == "claude-served-version"
    assert response.runtime_model.confirmed_version is UnknownModelIdentity.UNVERIFIED_BACKEND
    assert response.usage.input_tokens == 20
    assert response.usage.output_tokens == 4
    assert response.usage.total_tokens == 24
    assert opener.request is not None
    assert opener.request.full_url == "https://api.anthropic.com/v1/messages"
    assert opener.request.get_header("Anthropic-version") == "2023-06-01"
    assert opener.request.get_header("X-api-key") == "test-secret"
    sent = json.loads(opener.request.data or b"")
    assert sent["model"] == "claude-requested"
    assert sent["max_tokens"] == 1024
    assert sent["output_config"]["format"] == {
        "type": "json_schema",
        "schema": _request(tmp_path).response_schema,
    }
    assert "stock = 0" in sent["messages"][0]["content"]
    assert "tools" not in sent
    assert opener.timeout == 8


def test_generate_records_unknown_identity_and_untrusted_tool_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret")
    tool = {"type": "tool_use", "id": "untrusted", "name": "run", "input": {}}
    opener = FakeOpener(
        _response(
            model=None,
            content=[{"type": "text", "text": '{"candidate": null}'}, tool],
            usage={"input_tokens": -1, "output_tokens": 4},
        )
    )
    provider = AnthropicProvider(model="claude-alias")
    provider._opener = opener

    response = provider.generate(_request(tmp_path))

    assert response.runtime_model.confirmed_model is UnknownModelIdentity.NO_TELEMETRY
    assert response.runtime_model.confirmed_version is UnknownModelIdentity.NO_TELEMETRY
    assert response.tool_calls == (tool,)
    assert response.usage.input_tokens is None
    assert response.usage.output_tokens == 4
    assert response.usage.total_tokens is None


@pytest.mark.parametrize(
    ("stop_reason", "message"),
    [
        ("max_tokens", "truncated"),
        ("refusal", "refused"),
        ("tool_use", "did not complete"),
        (None, "did not complete"),
    ],
)
def test_generate_rejects_noncompleted_responses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stop_reason: str | None,
    message: str,
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret")
    provider = AnthropicProvider(model="claude-alias")
    provider._opener = FakeOpener(_response(stop_reason=stop_reason))

    with pytest.raises(ValueError, match=message):
        provider.generate(_request(tmp_path))


@pytest.mark.parametrize(
    "content",
    [
        None,
        [],
        [{"type": "thinking", "thinking": "hidden"}],
        [
            {"type": "text", "text": '{"candidate": null}'},
            {"type": "text", "text": '{"candidate": null}'},
        ],
    ],
)
def test_generate_rejects_ambiguous_content_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: object
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret")
    provider = AnthropicProvider(model="claude-alias")
    provider._opener = FakeOpener(_response(content=content))

    with pytest.raises((TypeError, ValueError)):
        provider.generate(_request(tmp_path))


def test_missing_or_malformed_credential_fails_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    with pytest.raises(ValueError, match="environment name"):
        AnthropicProvider(model="claude-alias", api_key_env="INVALID-NAME")

    provider = AnthropicProvider(
        model="claude-alias",
        api_key_env="EXHIBIT_A_MISSING_ANTHROPIC_KEY",
    )
    opener = FakeOpener({})
    provider._opener = opener
    with pytest.raises(RuntimeError, match="is not set"):
        provider.generate(_request(tmp_path))
    assert opener.request is None

    monkeypatch.setenv("EXHIBIT_A_MISSING_ANTHROPIC_KEY", "bad\nvalue")
    with pytest.raises(ValueError, match="invalid characters"):
        provider.generate(_request(tmp_path))
    assert opener.request is None


def test_provider_disables_redirects_and_ambient_proxies(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:8080")

    provider = AnthropicProvider(model="claude-alias")

    assert not any(isinstance(handler, ProxyHandler) for handler in provider._opener.handlers)
    redirect_handlers = [
        handler for handler in provider._opener.handlers if isinstance(handler, HTTPRedirectHandler)
    ]
    assert len(redirect_handlers) == 1
    assert (
        redirect_handlers[0].redirect_request(None, None, 302, "Found", {}, "https://other") is None
    )


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("max_tokens", 8193),
        ("max_tokens", True),
        ("timeout_s", 601),
        ("timeout_s", float("nan")),
        ("max_response_bytes", 2_000_001),
        ("max_response_bytes", 1.5),
        ("max_context_bytes", 200_001),
        ("max_context_bytes", True),
    ],
)
def test_provider_enforces_hard_operational_limits(option: str, value: object):
    with pytest.raises(ValueError, match="at most"):
        AnthropicProvider(model="claude-alias", **{option: value})


def test_generate_rejects_request_and_response_above_hard_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret")
    provider = AnthropicProvider(model="claude-alias")
    opener = FakeOpener({})
    provider._opener = opener
    with pytest.raises(ValueError, match="hard size limit"):
        provider.generate(_request(tmp_path, prompt="x" * 600_000))
    assert opener.request is None

    provider = AnthropicProvider(model="claude-alias", max_response_bytes=4)
    provider._opener = FakeOpener(_response())
    with pytest.raises(ValueError, match="size limit"):
        provider.generate(_request(tmp_path))


def test_generate_translates_redirect_without_following_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret")

    class RedirectingOpener:
        def open(self, request: Request, *, timeout: float):
            raise urllib.error.HTTPError(request.full_url, 302, "Found", {}, None)

    provider = AnthropicProvider(model="claude-alias")
    provider._opener = RedirectingOpener()

    with pytest.raises(RuntimeError, match="HTTP 302"):
        provider.generate(_request(tmp_path))
