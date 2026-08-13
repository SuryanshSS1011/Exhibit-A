from __future__ import annotations

import json
from pathlib import Path

import pytest

from exhibit_a.cli import _build_engine, main
from exhibit_a.providers import (
    OllamaProvider,
    ProviderKind,
    ProviderRole,
    load_provider_config,
)


def _write_config(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "providers.json"
    path.write_text(json.dumps(payload))
    return path


def test_config_selects_only_role_authorized_provider(tmp_path: Path):
    path = _write_config(
        tmp_path,
        {
            "providers": {
                "cloud": {
                    "type": "codex_cli",
                    "model": "gpt-5.6-sol",
                    "roles": ["proposer"],
                },
                "local": {
                    "type": "ollama",
                    "model": "qwen3:8b",
                    "base_url": "http://127.0.0.1:11434/v1",
                    "roles": ["proposer"],
                },
            },
            "roles": {"proposer": "local"},
        },
    )

    config = load_provider_config(path)

    assert isinstance(config.provider_for(ProviderRole.PROPOSER), OllamaProvider)
    assert config.providers["cloud"].kind is ProviderKind.CODEX_CLI


def test_config_cannot_assign_a_model_as_verifier(tmp_path: Path):
    path = _write_config(
        tmp_path,
        {
            "providers": {
                "local": {
                    "type": "ollama",
                    "model": "qwen3:8b",
                    "roles": ["proposer", "verifier"],
                }
            },
            "roles": {"proposer": "local", "verifier": "local"},
        },
    )

    with pytest.raises(ValueError, match="deterministic verifier is not pluggable"):
        load_provider_config(path)


def test_repro_engine_uses_configured_proposer(tmp_path: Path):
    path = _write_config(
        tmp_path,
        {
            "providers": {
                "local": {
                    "type": "ollama",
                    "model": "qwen3:8b",
                    "roles": ["proposer"],
                }
            },
            "roles": {"proposer": "local"},
        },
    )

    engine = _build_engine(False, False, provider_config=path)

    assert isinstance(engine.generator.provider, OllamaProvider)
    assert engine.generator.model == "qwen3:8b"


def test_offline_mode_does_not_silently_ignore_provider_config(tmp_path: Path):
    path = _write_config(tmp_path, {"providers": {}, "roles": {}})

    with pytest.raises(ValueError, match="cannot be combined"):
        _build_engine(False, True, provider_config=path)


def test_config_validates_options_for_unassigned_providers(tmp_path: Path):
    path = _write_config(
        tmp_path,
        {
            "providers": {
                "local": {
                    "type": "ollama",
                    "model": "qwen3:8b",
                    "roles": ["proposer"],
                    "max_context_bytes": True,
                },
                "codex": {
                    "type": "codex_cli",
                    "model": "gpt-5.6-sol",
                    "roles": ["proposer"],
                },
            },
            "roles": {"proposer": "codex"},
        },
    )

    with pytest.raises(ValueError, match="positive integer"):
        load_provider_config(path)


def test_config_rejects_remote_endpoint_even_when_provider_is_unassigned(tmp_path: Path):
    path = _write_config(
        tmp_path,
        {
            "providers": {
                "local": {
                    "type": "ollama",
                    "model": "qwen3:8b",
                    "roles": ["proposer"],
                    "base_url": "https://models.example.com/v1",
                },
                "codex": {
                    "type": "codex_cli",
                    "model": "gpt-5.6-sol",
                    "roles": ["proposer"],
                },
            },
            "roles": {"proposer": "codex"},
        },
    )

    with pytest.raises(ValueError, match="loopback"):
        load_provider_config(path)


def test_config_rejects_duplicate_and_normalized_provider_names(tmp_path: Path):
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"providers": {}, "providers": {}, "roles": {}}')
    with pytest.raises(ValueError, match="duplicate key"):
        load_provider_config(duplicate)

    normalized = _write_config(
        tmp_path,
        {
            "providers": {
                "local": {"type": "ollama", "model": "a", "roles": ["proposer"]},
                " local ": {"type": "ollama", "model": "b", "roles": ["proposer"]},
            },
            "roles": {"proposer": "local"},
        },
    )
    with pytest.raises(ValueError, match="collide"):
        load_provider_config(normalized)


def test_config_rejects_non_finite_json_numbers(tmp_path: Path):
    path = tmp_path / "providers.json"
    path.write_text(
        '{"providers":{"local":{"type":"ollama","model":"qwen3:8b",'
        '"roles":["proposer"],"timeout_s":NaN}},"roles":{"proposer":"local"}}'
    )

    with pytest.raises(ValueError, match="non-standard JSON constant"):
        load_provider_config(path)


def test_provider_authorization_maps_are_immutable(tmp_path: Path):
    path = _write_config(
        tmp_path,
        {
            "providers": {"local": {"type": "ollama", "model": "a", "roles": ["proposer"]}},
            "roles": {"proposer": "local"},
        },
    )
    config = load_provider_config(path)

    with pytest.raises(TypeError):
        config.role_assignments[ProviderRole.PROPOSER] = "replacement"  # type: ignore[index]


def test_replay_rejects_provider_config_instead_of_silently_ignoring_it(capsys):
    case = Path(__file__).parents[2] / "fixtures" / "cases" / "inventory_proven.json"

    assert main(["repro", "--replay", str(case), "--provider-config", "missing.json"]) == 2
    assert "cannot be combined" in capsys.readouterr().err
    assert main(["repro", "--replay", str(case), "--provider-config", ""]) == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_config_requires_proposer_assignment(tmp_path: Path):
    path = _write_config(
        tmp_path,
        {
            "providers": {"local": {"type": "ollama", "model": "a", "roles": ["proposer"]}},
            "roles": {},
        },
    )

    with pytest.raises(ValueError, match="requires a proposer"):
        load_provider_config(path)
