"""Strict JSON configuration for proposer-role model providers."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .anthropic import AnthropicProvider
from .base import Provider
from .codex_cli import CodexCliProvider
from .ollama import OllamaProvider
from .openai_compatible import OpenAICompatibleProvider


class ProviderRole(StrEnum):
    PROPOSER = "proposer"


class ProviderKind(StrEnum):
    ANTHROPIC = "anthropic"
    CODEX_CLI = "codex_cli"
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    kind: ProviderKind
    model: str
    roles: frozenset[ProviderRole]
    options: Mapping[str, Any]


@dataclass(frozen=True)
class ProviderConfig:
    providers: Mapping[str, ProviderSpec]
    role_assignments: Mapping[ProviderRole, str]

    def provider_for(self, role: ProviderRole) -> Provider:
        provider_name = self.role_assignments.get(role)
        if provider_name is None:
            raise ValueError(f"no provider is assigned to role {role.value!r}")
        spec = self.providers[provider_name]
        if role not in spec.roles:
            raise ValueError(f"provider {provider_name!r} is not allowed to serve {role.value!r}")
        return _provider_from_spec(spec)


def load_provider_config(path: str | Path) -> ProviderConfig:
    config_path = Path(path)
    payload = json.loads(
        config_path.read_text(),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_json_constant,
    )
    if not isinstance(payload, dict):
        raise TypeError("provider config must contain a JSON object")
    unexpected_root = set(payload) - {"providers", "roles"}
    if unexpected_root:
        raise ValueError(f"provider config has unexpected keys: {sorted(unexpected_root)!r}")

    raw_providers = payload.get("providers")
    raw_assignments = payload.get("roles")
    if not isinstance(raw_providers, dict) or not raw_providers:
        raise ValueError("provider config requires a non-empty providers object")
    if not isinstance(raw_assignments, dict):
        raise TypeError("provider config requires a roles object")

    providers: dict[str, ProviderSpec] = {}
    for name, raw in raw_providers.items():
        normalized_name = _nonempty_string(name, "provider name")
        if normalized_name in providers:
            raise ValueError(f"provider names collide after normalization: {normalized_name!r}")
        providers[normalized_name] = _parse_provider(normalized_name, raw)
    assignments: dict[ProviderRole, str] = {}
    for raw_role, raw_provider_name in raw_assignments.items():
        try:
            role = ProviderRole(_nonempty_string(raw_role, "provider role"))
        except ValueError as exc:
            raise ValueError(
                f"unsupported model role {raw_role!r}; only proposer is configurable and "
                "the deterministic verifier is not pluggable"
            ) from exc
        provider_name = _nonempty_string(raw_provider_name, f"provider for {role.value}")
        if provider_name not in providers:
            raise ValueError(f"role {role.value!r} references unknown provider {provider_name!r}")
        if role not in providers[provider_name].roles:
            raise ValueError(f"provider {provider_name!r} is not allowed to serve {role.value!r}")
        assignments[role] = provider_name
    if ProviderRole.PROPOSER not in assignments:
        raise ValueError("provider config requires a proposer role assignment")
    for spec in providers.values():
        _provider_from_spec(spec)
    return ProviderConfig(MappingProxyType(providers), MappingProxyType(assignments))


def _parse_provider(name: object, raw: object) -> ProviderSpec:
    provider_name = _nonempty_string(name, "provider name")
    if not isinstance(raw, dict):
        raise TypeError(f"provider {provider_name!r} must contain an object")
    kind_value = _nonempty_string(raw.get("type"), f"type for provider {provider_name!r}")
    try:
        kind = ProviderKind(kind_value)
    except ValueError as exc:
        raise ValueError(f"provider {provider_name!r} has unsupported type {kind_value!r}") from exc
    model = _nonempty_string(raw.get("model"), f"model for provider {provider_name!r}")
    raw_roles = raw.get("roles")
    if not isinstance(raw_roles, list) or not raw_roles:
        raise ValueError(f"provider {provider_name!r} requires a non-empty roles list")
    try:
        roles = frozenset(
            ProviderRole(_nonempty_string(role, "provider role")) for role in raw_roles
        )
    except ValueError as exc:
        raise ValueError(
            f"provider {provider_name!r} declares an unsupported model role; "
            "only proposer is configurable and the deterministic verifier is not pluggable"
        ) from exc

    common = {"type", "model", "roles", "timeout_s"}
    kind_options = {
        ProviderKind.ANTHROPIC: {
            "api_key_env",
            "max_context_bytes",
            "max_response_bytes",
            "max_tokens",
        },
        ProviderKind.CODEX_CLI: {"codex_bin"},
        ProviderKind.OLLAMA: {"base_url", "max_response_bytes", "max_context_bytes"},
        ProviderKind.OPENAI_COMPATIBLE: {
            "api_key_env",
            "base_url",
            "max_context_bytes",
            "max_response_bytes",
        },
    }[kind]
    unexpected = set(raw) - common - kind_options
    if unexpected:
        raise ValueError(f"provider {provider_name!r} has unexpected keys: {sorted(unexpected)!r}")
    options = {key: raw[key] for key in kind_options | {"timeout_s"} if key in raw}
    _validate_options(provider_name, options)
    return ProviderSpec(provider_name, kind, model, roles, MappingProxyType(options))


def _nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _validate_options(name: str, options: dict[str, Any]) -> None:
    timeout = options.get("timeout_s")
    if timeout is not None and (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError(f"provider {name!r} timeout_s must be a positive number")
    for key in ("max_context_bytes", "max_response_bytes", "max_tokens"):
        value = options.get(key)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
        ):
            raise ValueError(f"provider {name!r} {key} must be a positive integer")
    for key in ("api_key_env", "codex_bin", "base_url"):
        if key in options:
            options[key] = _nonempty_string(options[key], f"{key} for provider {name!r}")


def _provider_from_spec(spec: ProviderSpec) -> Provider:
    if spec.kind is ProviderKind.ANTHROPIC:
        return AnthropicProvider(model=spec.model, **spec.options)
    if spec.kind is ProviderKind.CODEX_CLI:
        return CodexCliProvider(model=spec.model, **spec.options)
    if spec.kind is ProviderKind.OLLAMA:
        return OllamaProvider(model=spec.model, **spec.options)
    if spec.kind is ProviderKind.OPENAI_COMPATIBLE:
        return OpenAICompatibleProvider(model=spec.model, **spec.options)
    raise ValueError(f"unsupported provider type {spec.kind!r}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"provider config contains duplicate key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"provider config contains non-standard JSON constant {value!r}")
