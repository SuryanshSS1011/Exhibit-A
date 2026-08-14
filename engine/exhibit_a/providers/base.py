"""Normalized request and response types shared by proposer-model providers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderRequest:
    """One structured-output request made by a proposer-role component."""

    prompt: str
    response_schema: dict[str, Any]
    repo_path: Path


class UnknownModelIdentity(StrEnum):
    """Why a provider could not confirm runtime model identity."""

    NO_TELEMETRY = "unknown_no_telemetry"
    UNVERIFIED_BACKEND = "unknown_unverified_backend"


@dataclass(frozen=True)
class RuntimeModel:
    """Requested and observed model identity for an individual response.

    Every adapter must explicitly populate the confirmed fields, including an
    ``UnknownModelIdentity`` reason when the backend does not provide trustworthy
    telemetry. Callers must never substitute the requested model for confirmation.
    """

    provider: str
    requested_model: str
    confirmed_model: str | UnknownModelIdentity
    confirmed_version: str | UnknownModelIdentity

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("runtime model provider must not be empty")
        if not self.requested_model.strip():
            raise ValueError("requested model must not be empty")
        if isinstance(self.confirmed_model, str) and not self.confirmed_model.strip():
            raise ValueError("confirmed model must not be empty")
        if isinstance(self.confirmed_version, str) and not self.confirmed_version.strip():
            raise ValueError("confirmed model version must not be empty")
        reserved = {item.value for item in UnknownModelIdentity}
        if type(self.confirmed_model) is str and self.confirmed_model in reserved:
            raise ValueError("confirmed model cannot impersonate an unknown identity sentinel")
        if type(self.confirmed_version) is str and self.confirmed_version in reserved:
            raise ValueError(
                "confirmed model version cannot impersonate an unknown identity sentinel"
            )


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class ProviderResponse:
    """Provider-independent structured output and available telemetry."""

    output: dict[str, Any]
    runtime_model: RuntimeModel
    tool_calls: tuple[dict[str, Any], ...] = ()
    usage: TokenUsage = field(default_factory=TokenUsage)
    cost_usd: float | None = None
    latency_ms: float | None = None

    @property
    def output_sha256(self) -> str:
        canonical = json.dumps(
            self.output,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        return hashlib.sha256(canonical).hexdigest()


class Provider(Protocol):
    """A proposer-model transport; never a verdict or evidence judge.

    Implementations treat model output as untrusted and own the containment required
    by their transport. CLI adapters must sandbox child processes; HTTP adapters must
    not execute returned tool calls and must scope network and credential access.
    """

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Return normalized structured output without modifying the repository."""
        ...
