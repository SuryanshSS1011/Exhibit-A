from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from pathlib import Path
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request

import pytest

from exhibit_a.providers import (
    OpenAICompatibleProvider,
    ProviderRequest,
    UnknownModelIdentity,
    ollama,
)


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


def _request(repo_path: Path) -> ProviderRequest:
    return ProviderRequest(
        prompt="return a candidate",
        response_schema={"type": "object", "required": ["candidate"]},
        repo_path=repo_path,
    )


def test_generate_uses_portable_chat_contract_and_environment_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "inventory.py").write_text("stock = 0\n")
    monkeypatch.setenv("EXHIBIT_A_TEST_KEY", "test-secret")
    opener = FakeOpener(
        {
            "model": "served-model",
            "choices": [{"message": {"content": '{"candidate": null}'}}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11},
        }
    )
    provider = OpenAICompatibleProvider(
        model="requested-model",
        base_url="https://models.example.com/v1",
        api_key_env="EXHIBIT_A_TEST_KEY",
        timeout_s=9,
    )
    provider._opener = opener

    response = provider.generate(_request(tmp_path))

    assert response.output == {"candidate": None}
    assert response.runtime_model.provider == "openai_compatible"
    assert response.runtime_model.requested_model == "requested-model"
    assert response.runtime_model.confirmed_model == "served-model"
    assert response.runtime_model.confirmed_version is UnknownModelIdentity.UNVERIFIED_BACKEND
    assert response.usage.total_tokens == 11
    assert opener.request is not None
    assert opener.request.full_url == "https://models.example.com/v1/chat/completions"
    assert opener.request.get_header("Authorization") == "Bearer test-secret"
    sent = json.loads(opener.request.data or b"")
    assert sent["model"] == "requested-model"
    assert sent["response_format"]["json_schema"]["schema"] == _request(tmp_path).response_schema
    assert "stock = 0" in sent["messages"][0]["content"]
    assert "tools" not in sent
    assert opener.timeout == 9


def test_generate_supports_unauthenticated_loopback_and_explicit_unknown_identity(
    tmp_path: Path,
):
    opener = FakeOpener({"choices": [{"message": {"content": '{"candidate": null}'}}]})
    provider = OpenAICompatibleProvider(
        model="local-alias",
        base_url="http://127.0.0.1:8000/v1",
    )
    provider._opener = opener

    response = provider.generate(_request(tmp_path))

    assert response.runtime_model.confirmed_model is UnknownModelIdentity.NO_TELEMETRY
    assert response.runtime_model.confirmed_version is UnknownModelIdentity.NO_TELEMETRY
    assert opener.request is not None
    assert opener.request.get_header("Authorization") is None


def test_generate_discards_invalid_negative_usage_telemetry(tmp_path: Path):
    provider = OpenAICompatibleProvider(
        model="model",
        base_url="https://models.example.com/v1",
    )
    provider._opener = FakeOpener(
        {
            "choices": [{"message": {"content": '{"candidate": null}'}}],
            "usage": {"prompt_tokens": -1, "completion_tokens": 2, "total_tokens": -3},
        }
    )

    response = provider.generate(_request(tmp_path))

    assert response.usage.input_tokens is None
    assert response.usage.output_tokens == 2
    assert response.usage.total_tokens is None


@pytest.mark.parametrize(
    "base_url",
    [
        "http://models.example.com/v1",
        "http://localhost:8000/v1",
        "file:///tmp/models",
        "https://user:secret@models.example.com/v1",
        "https://models.example.com/v1?tenant=secret",
        "https://models.example.com/v1#fragment",
        "https://models.example.com:invalid/v1",
        " https://models.example.com/v1",
        "https://models.example.com/v1\n",
    ],
)
def test_provider_rejects_insecure_or_ambiguous_endpoints(base_url: str):
    with pytest.raises(ValueError):
        OpenAICompatibleProvider(model="model", base_url=base_url)


def test_provider_disables_redirects_and_ambient_proxies(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:8080")

    provider = OpenAICompatibleProvider(
        model="model",
        base_url="https://models.example.com/v1",
    )

    proxy_handlers = [
        handler for handler in provider._opener.handlers if isinstance(handler, ProxyHandler)
    ]
    assert proxy_handlers == []
    redirect_handlers = [
        handler for handler in provider._opener.handlers if isinstance(handler, HTTPRedirectHandler)
    ]
    assert len(redirect_handlers) == 1
    assert (
        redirect_handlers[0].redirect_request(None, None, 302, "Found", {}, "https://other") is None
    )


def test_missing_or_malformed_environment_credential_fails_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    with pytest.raises(ValueError, match="environment name"):
        OpenAICompatibleProvider(
            model="model",
            base_url="https://models.example.com/v1",
            api_key_env="INVALID-NAME",
        )

    provider = OpenAICompatibleProvider(
        model="model",
        base_url="https://models.example.com/v1",
        api_key_env="EXHIBIT_A_MISSING_KEY",
    )
    opener = FakeOpener({})
    provider._opener = opener
    with pytest.raises(RuntimeError, match="is not set"):
        provider.generate(_request(tmp_path))
    assert opener.request is None

    monkeypatch.setenv("EXHIBIT_A_MISSING_KEY", "bad\nvalue")
    with pytest.raises(ValueError, match="invalid characters"):
        provider.generate(_request(tmp_path))
    assert opener.request is None


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("timeout_s", 601),
        ("timeout_s", True),
        ("max_response_bytes", 2_000_001),
        ("max_response_bytes", 1.5),
        ("max_context_bytes", 200_001),
        ("max_context_bytes", True),
    ],
)
def test_provider_enforces_hard_operational_limits(option: str, value: object):
    with pytest.raises(ValueError, match="at most"):
        OpenAICompatibleProvider(
            model="model",
            base_url="https://models.example.com/v1",
            **{option: value},
        )


def test_repository_snapshot_rejects_file_swapped_to_external_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "visible.py"
    source.write_text("public = True\n")
    secret = tmp_path.parent / "provider-context-secret.py"
    secret.write_text("secret = 'must not leave machine'\n")
    original_paths = ollama._context_paths

    def swap_after_discovery(root_descriptor: int) -> list[Path]:
        paths = original_paths(root_descriptor)
        source.unlink()
        source.symlink_to(secret)
        return paths

    monkeypatch.setattr(ollama, "_context_paths", swap_after_discovery)
    provider = OpenAICompatibleProvider(
        model="model",
        base_url="https://models.example.com/v1",
    )
    opener = FakeOpener({"choices": [{"message": {"content": '{"candidate": null}'}}]})
    provider._opener = opener

    provider.generate(_request(tmp_path))

    assert opener.request is not None
    sent = json.loads(opener.request.data or b"")
    assert "must not leave machine" not in sent["messages"][0]["content"]


def test_repository_snapshot_enforces_traversal_budget_before_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    for index in range(3):
        (tmp_path / f"file-{index}.py").write_text("value = 1\n")
    monkeypatch.setattr(ollama, "_MAX_CONTEXT_ENTRIES", 2)
    provider = OpenAICompatibleProvider(
        model="model",
        base_url="https://models.example.com/v1",
    )
    opener = FakeOpener({})
    provider._opener = opener

    with pytest.raises(ValueError, match="too many entries"):
        provider.generate(_request(tmp_path))

    assert opener.request is None


@pytest.mark.parametrize("reserved", list(UnknownModelIdentity))
def test_backend_cannot_impersonate_unknown_identity_sentinel(
    tmp_path: Path, reserved: UnknownModelIdentity
):
    provider = OpenAICompatibleProvider(
        model="model",
        base_url="https://models.example.com/v1",
    )
    provider._opener = FakeOpener(
        {
            "model": reserved.value,
            "choices": [{"message": {"content": '{"candidate": null}'}}],
        }
    )

    with pytest.raises(ValueError, match="impersonate"):
        provider.generate(_request(tmp_path))


def test_generate_rejects_oversized_and_redirect_responses(tmp_path: Path):
    provider = OpenAICompatibleProvider(
        model="model",
        base_url="https://models.example.com/v1",
        max_response_bytes=4,
    )
    provider._opener = FakeOpener({"choices": []})
    with pytest.raises(ValueError, match="size limit"):
        provider.generate(_request(tmp_path))

    class RedirectingOpener:
        def open(self, request: Request, *, timeout: float):
            raise urllib.error.HTTPError(request.full_url, 302, "Found", {}, None)

    provider = OpenAICompatibleProvider(
        model="model",
        base_url="https://models.example.com/v1",
    )
    provider._opener = RedirectingOpener()
    with pytest.raises(RuntimeError, match="HTTP 302"):
        provider.generate(_request(tmp_path))


def test_generate_rejects_oversized_request_before_network(tmp_path: Path):
    provider = OpenAICompatibleProvider(
        model="model",
        base_url="https://models.example.com/v1",
    )
    opener = FakeOpener({})
    provider._opener = opener
    request = ProviderRequest("x" * 600_000, {"type": "object"}, tmp_path)

    with pytest.raises(ValueError, match="hard size limit"):
        provider.generate(request)

    assert opener.request is None
