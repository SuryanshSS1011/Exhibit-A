from __future__ import annotations

import json
import subprocess

import pytest

from exhibit_a.cli import main
from exhibit_a.replay_environment import (
    PYTEST_ARTIFACT_LABEL,
    PYTEST_VERSION_LABEL,
    inspect_local_replay_image,
    parse_replay_environment,
)


DIGEST = "sha256:" + "a" * 64
ARTIFACT = "sha256:" + "b" * 64


def _descriptor(**image_overrides):
    image = {
        "architecture": "amd64",
        "digest": DIGEST,
        "os": "linux",
        "reference": "registry.example/exhibit-a/replay-python",
        "variant": None,
        **image_overrides,
    }
    return {
        "image": image,
        "pytest": {"artifactSha256": ARTIFACT, "version": "8.4.1"},
        "schemaVersion": "eef-replay-environment/v1",
    }


def _inspection(environment, **overrides):
    return {
        "Architecture": environment.architecture,
        "Config": {
            "Labels": {
                PYTEST_ARTIFACT_LABEL: environment.pytest_artifact_sha256,
                PYTEST_VERSION_LABEL: environment.pytest_version,
            }
        },
        "Os": environment.os,
        "RepoDigests": [environment.image],
        "Variant": environment.variant or "",
        **overrides,
    }


def test_local_image_inspection_checks_digest_platform_and_runtime(monkeypatch):
    environment = parse_replay_environment(_descriptor())
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps([_inspection(environment)]), "")

    monkeypatch.setattr("exhibit_a.replay_environment.subprocess.run", fake_run)
    inspect_local_replay_image(environment)

    assert calls == [["docker", "image", "inspect", environment.image]]
    assert all("pull" not in argument for argument in calls[0])


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"RepoDigests": ["registry.example/other@" + DIGEST]}, "digest"),
        ({"Architecture": "arm64"}, "platform"),
        ({"Os": "darwin"}, "platform"),
        ({"Variant": "v8"}, "variant"),
        ({"Config": {"Labels": {}}}, "pytest version"),
        (
            {
                "Config": {
                    "Labels": {
                        PYTEST_ARTIFACT_LABEL: "sha256:" + "f" * 64,
                        PYTEST_VERSION_LABEL: "8.4.1",
                    }
                }
            },
            "pytest artifact",
        ),
    ],
)
def test_local_image_identity_mismatches_fail_closed(monkeypatch, overrides, message):
    environment = parse_replay_environment(_descriptor())
    monkeypatch.setattr(
        "exhibit_a.replay_environment.subprocess.run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, json.dumps([_inspection(environment, **overrides)]), ""
        ),
    )

    with pytest.raises(RuntimeError, match=message):
        inspect_local_replay_image(environment)


def test_missing_local_digest_is_an_offline_prerequisite(monkeypatch):
    environment = parse_replay_environment(_descriptor())
    monkeypatch.setattr(
        "exhibit_a.replay_environment.subprocess.run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, "", "not found"),
    )

    with pytest.raises(RuntimeError, match="prerequisite missing.*will not pull"):
        inspect_local_replay_image(environment)


@pytest.mark.parametrize(
    "override",
    [
        {"reference": "https://registry.example/image"},
        {"reference": "image@sha256:" + "a" * 64},
        {"reference": "registry.example/image:latest"},
        {"reference": "Registry.example/image"},
        {"digest": "sha256:short"},
        {"architecture": "AMD64"},
    ],
)
def test_invalid_descriptor_values_are_rejected(override):
    with pytest.raises(ValueError):
        parse_replay_environment(_descriptor(**override))


def test_lock_command_separates_an_intentional_refresh_from_replay(monkeypatch, tmp_path):
    output = tmp_path / "replay-environment.json"
    inspected = []
    monkeypatch.setattr(
        "exhibit_a.cli.inspect_local_replay_image",
        lambda environment, **kwargs: inspected.append((environment, kwargs)),
    )

    result = main(
        [
            "lock-replay-environment",
            "--reference",
            "registry.example/exhibit-a/replay-python",
            "--digest",
            DIGEST,
            "--architecture",
            "amd64",
            "--pytest-artifact-sha256",
            ARTIFACT,
            "--out",
            str(output),
        ]
    )

    assert result == 0
    assert json.loads(output.read_text()) == _descriptor()
    assert inspected[0][0].image.endswith("@" + DIGEST)
    assert inspected[0][1] == {"docker_bin": "docker"}

    assert (
        main(
            [
                "lock-replay-environment",
                "--reference",
                "registry.example/exhibit-a/replay-python",
                "--digest",
                DIGEST,
                "--architecture",
                "amd64",
                "--pytest-artifact-sha256",
                ARTIFACT,
                "--out",
                str(output),
            ]
        )
        == 2
    )
