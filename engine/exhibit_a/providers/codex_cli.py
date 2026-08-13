"""Read-only Codex CLI provider adapter."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .base import ProviderRequest, ProviderResponse, RuntimeModel, UnknownModelIdentity

_CODEX_BIN_ENV = "EXHIBIT_A_CODEX_BIN"


def _default_codex_paths() -> tuple[Path, ...]:
    return (
        Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
        Path.home() / ".local" / "bin" / "codex",
    )


def _resolve_codex_binary(configured: str | None = None) -> str:
    requested = configured or os.environ.get(_CODEX_BIN_ENV)
    if requested:
        resolved = shutil.which(requested)
        if resolved:
            return resolved
        raise RuntimeError(
            f"Codex CLI not found at {requested!r}. Set {_CODEX_BIN_ENV} to the "
            "executable path, or install the Codex CLI and add it to PATH."
        )

    resolved = shutil.which("codex")
    if resolved:
        return resolved
    for candidate in _default_codex_paths():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise RuntimeError(
        f"Codex CLI not found on PATH or in a known app location. Set {_CODEX_BIN_ENV} "
        "to the executable path (for example, "
        "/Applications/ChatGPT.app/Contents/Resources/codex)."
    )


class CodexCliProvider:
    """Invoke Codex in a read-only sandbox and normalize its structured output."""

    def __init__(
        self,
        *,
        codex_bin: str | None = None,
        model: str | None = None,
        timeout_s: int = 240,
    ):
        self.codex_bin = codex_bin
        self.model = model or os.environ.get("EXHIBIT_A_MODEL", "gpt-5.6-sol")
        self.timeout_s = timeout_s

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        repo = request.repo_path.resolve()
        if not repo.is_dir():
            raise ValueError(f"repo checkout not found: {repo}")

        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="exhibit-a-codex-") as tmp:
            tmp_path = Path(tmp)
            schema_path = tmp_path / "schema.json"
            output_path = tmp_path / "response.json"
            schema_path.write_text(json.dumps(request.response_schema))

            argv = [
                _resolve_codex_binary(self.codex_bin),
                "exec",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--model",
                self.model,
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "--cd",
                str(repo),
                "-",
            ]
            proc = subprocess.run(
                argv,
                input=request.prompt,
                capture_output=True,
                check=False,
                text=True,
                timeout=self.timeout_s,
            )
            if proc.returncode != 0:
                detail = proc.stderr.strip() or proc.stdout.strip() or "no diagnostic"
                raise RuntimeError(f"Codex exited {proc.returncode}: {detail[-2000:]}")
            if not output_path.exists():
                raise ValueError("Codex produced no structured response")
            payload = json.loads(output_path.read_text())
            if not isinstance(payload, dict):
                raise TypeError("Codex response was not a JSON object")

        return ProviderResponse(
            output=payload,
            runtime_model=RuntimeModel(
                provider="openai-codex-cli",
                requested_model=self.model,
                confirmed_model=UnknownModelIdentity.NO_TELEMETRY,
                confirmed_version=UnknownModelIdentity.NO_TELEMETRY,
            ),
            latency_ms=(time.monotonic() - started) * 1000,
        )
