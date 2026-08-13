from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from urllib.request import ProxyHandler, Request

import pytest

from exhibit_a.providers import OllamaProvider, ProviderRequest, UnknownModelIdentity


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


def test_generate_normalizes_ollama_response_without_executing_tool_calls(tmp_path: Path):
    (tmp_path / "inventory.py").write_text("def stock_for(sku):\n    return 0\n")
    opener = FakeOpener(
        {
            "model": "qwen3:8b-q4_K_M",
            "choices": [
                {
                    "message": {
                        "content": '{"candidate": null}',
                        "tool_calls": [{"id": "untrusted", "function": {"name": "run"}}],
                    }
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        }
    )
    provider = OllamaProvider(model="qwen3:8b", timeout_s=7)
    provider._opener = opener

    response = provider.generate(_request(tmp_path))

    assert response.output == {"candidate": None}
    assert response.runtime_model.requested_model == "qwen3:8b"
    assert response.runtime_model.confirmed_model == "qwen3:8b-q4_K_M"
    assert response.runtime_model.confirmed_version is UnknownModelIdentity.UNVERIFIED_BACKEND
    assert response.usage.total_tokens == 16
    assert response.tool_calls == ({"id": "untrusted", "function": {"name": "run"}},)
    assert opener.request is not None
    assert opener.request.full_url == "http://127.0.0.1:11434/v1/chat/completions"
    sent = json.loads(opener.request.data or b"")
    sent_prompt = sent["messages"][0]["content"]
    assert "return a candidate" in sent_prompt
    assert "--- inventory.py ---" in sent_prompt
    assert "def stock_for(sku):" in sent_prompt
    assert sent["response_format"]["json_schema"]["schema"] == _request(tmp_path).response_schema
    assert "tools" not in sent
    assert opener.timeout == 7


@pytest.mark.parametrize(
    "base_url",
    [
        "https://models.example.com/v1",
        "http://192.168.1.12:11434/v1",
        "http://localhost:11434/v1",
        "file:///tmp/ollama",
        "http://user:secret@localhost:11434/v1",
        "http://localhost:11434/v1?redirect=https://example.com",
    ],
)
def test_ollama_rejects_non_loopback_or_ambiguous_endpoints(base_url: str):
    with pytest.raises(ValueError):
        OllamaProvider(model="qwen3:8b", base_url=base_url)


def test_generate_records_explicit_unknown_when_model_telemetry_is_missing(tmp_path: Path):
    opener = FakeOpener({"choices": [{"message": {"content": '{"candidate": null}'}}]})
    provider = OllamaProvider(model="local-alias")
    provider._opener = opener

    response = provider.generate(_request(tmp_path))

    assert response.runtime_model.confirmed_model is UnknownModelIdentity.NO_TELEMETRY
    assert response.runtime_model.confirmed_version is UnknownModelIdentity.NO_TELEMETRY


def test_ollama_opener_ignores_ambient_proxy_configuration(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.com:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:8080")

    provider = OllamaProvider(model="local-alias")

    proxy_handlers = [
        handler for handler in provider._opener.handlers if isinstance(handler, ProxyHandler)
    ]
    assert proxy_handlers == []


def test_generate_rejects_oversized_response(tmp_path: Path):
    provider = OllamaProvider(model="qwen3:8b", max_response_bytes=4)
    provider._opener = FakeOpener({"choices": []})

    with pytest.raises(ValueError, match="size limit"):
        provider.generate(_request(tmp_path))


def test_repository_snapshot_is_bounded_and_does_not_follow_symlinks(tmp_path: Path):
    (tmp_path / "visible.py").write_text("visible = 'included'\n")
    (tmp_path / "AAA.md").write_text("documentation consumes context\n" * 20)
    outside = tmp_path.parent / "outside-secret.py"
    outside.write_text("secret = 'do not include'\n")
    (tmp_path / "linked.py").symlink_to(outside)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("credential = secret\n")
    opener = FakeOpener({"choices": [{"message": {"content": '{"candidate": null}'}}]})
    provider = OllamaProvider(model="local-alias", max_context_bytes=80)
    provider._opener = opener

    provider.generate(_request(tmp_path))

    assert opener.request is not None
    sent = json.loads(opener.request.data or b"")
    prompt = sent["messages"][0]["content"]
    snapshot = prompt.split("REPOSITORY SNAPSHOT (untrusted, read-only):\n", 1)[1]
    assert len(snapshot.encode()) <= 80
    assert "visible = 'included'" in prompt
    assert "documentation consumes context" not in prompt
    assert "do not include" not in prompt
    assert "credential = secret" not in prompt


def test_generate_rejects_schema_invalid_or_truncated_output(tmp_path: Path):
    provider = OllamaProvider(model="local-alias")
    provider._opener = FakeOpener(
        {"choices": [{"finish_reason": "stop", "message": {"content": '{"wrong": true}'}}]}
    )
    with pytest.raises(ValueError, match="missing"):
        provider.generate(_request(tmp_path))

    provider._opener = FakeOpener(
        {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": '{"candidate": null}'},
                }
            ]
        }
    )
    with pytest.raises(ValueError, match="truncated"):
        provider.generate(_request(tmp_path))
