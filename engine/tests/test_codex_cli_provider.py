from __future__ import annotations

import json
from pathlib import Path

import pytest

from exhibit_a.providers import (
    CodexCliProvider,
    ProviderRequest,
    RuntimeModel,
    UnknownModelIdentity,
    codex_cli,
)


def test_generate_normalizes_output_without_inventing_runtime_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(codex_cli, "_resolve_codex_binary", lambda configured: "/bin/codex")
    invocation: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object):
        invocation["argv"] = argv
        invocation["kwargs"] = kwargs
        output_path = Path(argv[argv.index("--output-last-message") + 1])
        output_path.write_text(json.dumps({"answer": "ok"}))
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(codex_cli.subprocess, "run", fake_run)
    provider = CodexCliProvider(model="gpt-requested")

    response = provider.generate(ProviderRequest("prompt", {"type": "object"}, tmp_path))

    assert response.output == {"answer": "ok"}
    assert response.runtime_model.provider == "openai-codex-cli"
    assert response.runtime_model.requested_model == "gpt-requested"
    assert response.runtime_model.confirmed_model is UnknownModelIdentity.NO_TELEMETRY
    assert response.runtime_model.confirmed_version is UnknownModelIdentity.NO_TELEMETRY
    assert response.latency_ms is not None
    argv = invocation["argv"]
    assert isinstance(argv, list)
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("--cd") + 1] == str(tmp_path)
    kwargs = invocation["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["input"] == "prompt"


def test_codex_binary_honors_environment_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EXHIBIT_A_CODEX_BIN", "/opt/codex/bin/codex")
    monkeypatch.setattr(
        codex_cli.shutil,
        "which",
        lambda command: command if command == "/opt/codex/bin/codex" else None,
    )

    assert codex_cli._resolve_codex_binary() == "/opt/codex/bin/codex"


def test_runtime_model_requires_requested_identity():
    with pytest.raises(ValueError, match="requested model"):
        RuntimeModel(
            provider="provider",
            requested_model=" ",
            confirmed_model=UnknownModelIdentity.NO_TELEMETRY,
            confirmed_version=UnknownModelIdentity.NO_TELEMETRY,
        )


@pytest.mark.parametrize("field", ["confirmed_model", "confirmed_version"])
def test_runtime_model_rejects_plain_strings_that_impersonate_unknown_identity(field: str):
    values = {
        "provider": "provider",
        "requested_model": "requested",
        "confirmed_model": "served",
        "confirmed_version": "version",
    }
    values[field] = UnknownModelIdentity.NO_TELEMETRY.value

    with pytest.raises(ValueError, match="impersonate"):
        RuntimeModel(**values)


def test_codex_binary_uses_known_app_location(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    app_binary = tmp_path / "codex"
    app_binary.touch(mode=0o755)
    monkeypatch.delenv("EXHIBIT_A_CODEX_BIN", raising=False)
    monkeypatch.setattr(codex_cli.shutil, "which", lambda command: None)
    monkeypatch.setattr(codex_cli, "_default_codex_paths", lambda: (app_binary,))

    assert codex_cli._resolve_codex_binary() == str(app_binary)


def test_codex_binary_error_is_actionable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("EXHIBIT_A_CODEX_BIN", raising=False)
    monkeypatch.setattr(codex_cli.shutil, "which", lambda command: None)
    monkeypatch.setattr(codex_cli, "_default_codex_paths", tuple)

    with pytest.raises(RuntimeError, match="EXHIBIT_A_CODEX_BIN"):
        codex_cli._resolve_codex_binary()
