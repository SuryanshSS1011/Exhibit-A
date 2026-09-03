"""Immutable OCI replay-environment descriptors and offline local inspection."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


SCHEMA_VERSION = "eef-replay-environment/v1"
# The pytest a replay image is built with. Its failure renderer ends up inside the signed
# log, so a bump changes archived evidence and must be deliberate and made in one place.
# (The dogfood generators pin their own, separate pytest for the suite they archive.)
PINNED_PYTEST_VERSION = "8.4.1"
PYTEST_VERSION_LABEL = "dev.exhibit-a.pytest.version"
PYTEST_ARTIFACT_LABEL = "dev.exhibit-a.pytest.artifact-sha256"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REFERENCE = re.compile(r"[a-z0-9][a-z0-9._:/-]{0,254}\Z")
_PLATFORM_VALUE = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}\Z")


@dataclass(frozen=True)
class ReplayEnvironment:
    reference: str
    digest: str
    os: str
    architecture: str
    variant: str | None
    pytest_version: str
    pytest_artifact_sha256: str

    @property
    def image(self) -> str:
        return f"{self.reference}@{self.digest}"

    @property
    def platform(self) -> str:
        suffix = f"/{self.variant}" if self.variant else ""
        return f"{self.os}/{self.architecture}{suffix}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "image": {
                "architecture": self.architecture,
                "digest": self.digest,
                "os": self.os,
                "reference": self.reference,
                "variant": self.variant,
            },
            "pytest": {
                "artifactSha256": self.pytest_artifact_sha256,
                "version": self.pytest_version,
            },
            "schemaVersion": SCHEMA_VERSION,
        }


def parse_replay_environment(value: object) -> ReplayEnvironment:
    """Validate the exact v1 descriptor shape."""
    if not isinstance(value, Mapping) or set(value) != {"image", "pytest", "schemaVersion"}:
        raise ValueError("EEF replay environment has an invalid shape")
    if value.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("EEF replay environment schema is unsupported")
    image = value.get("image")
    pytest = value.get("pytest")
    if not isinstance(image, Mapping) or set(image) != {
        "architecture",
        "digest",
        "os",
        "reference",
        "variant",
    }:
        raise ValueError("EEF replay image descriptor is invalid")
    if not isinstance(pytest, Mapping) or set(pytest) != {"artifactSha256", "version"}:
        raise ValueError("EEF pytest descriptor is invalid")
    reference = image.get("reference")
    digest = image.get("digest")
    os_name = image.get("os")
    architecture = image.get("architecture")
    variant = image.get("variant")
    pytest_version = pytest.get("version")
    artifact = pytest.get("artifactSha256")
    if (
        not isinstance(reference, str)
        or not _REFERENCE.fullmatch(reference)
        or "@" in reference
        or "//" in reference
        or ":" in reference.rsplit("/", 1)[-1]
        or not isinstance(digest, str)
        or not _DIGEST.fullmatch(digest)
    ):
        raise ValueError("EEF replay image identity is invalid")
    if (
        not isinstance(os_name, str)
        or not _PLATFORM_VALUE.fullmatch(os_name)
        or not isinstance(architecture, str)
        or not _PLATFORM_VALUE.fullmatch(architecture)
        or not (variant is None or isinstance(variant, str) and _PLATFORM_VALUE.fullmatch(variant))
    ):
        raise ValueError("EEF replay platform is invalid")
    if pytest_version != PINNED_PYTEST_VERSION:
        raise ValueError("EEF replay pytest version is unsupported")
    if not isinstance(artifact, str) or not _DIGEST.fullmatch(artifact):
        raise ValueError("EEF replay pytest artifact digest is invalid")
    return ReplayEnvironment(
        reference,
        digest,
        os_name,
        architecture,
        variant,
        pytest_version,
        artifact,
    )


def inspect_local_replay_image(
    environment: ReplayEnvironment,
    *,
    docker_bin: str = "docker",
) -> None:
    """Require the exact image, platform, and runtime labels without pulling."""
    try:
        result = subprocess.run(
            [docker_bin, "image", "inspect", environment.image],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("offline replay image inspection timed out") from error
    except OSError as error:
        raise RuntimeError("Docker is unavailable for offline replay image inspection") from error
    if result.returncode != 0:
        raise RuntimeError(
            "offline replay prerequisite missing: import "
            f"{environment.image}; verification will not pull it"
        )
    try:
        records = json.loads(result.stdout)
        record = records[0]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("Docker returned invalid replay image metadata") from error
    if not isinstance(record, dict):
        raise RuntimeError("Docker returned invalid replay image metadata")
    repo_digests = record.get("RepoDigests")
    if not isinstance(repo_digests, list) or environment.image not in repo_digests:
        raise RuntimeError("local replay image digest does not match the signed descriptor")
    if record.get("Os") != environment.os or record.get("Architecture") != environment.architecture:
        raise RuntimeError(
            "local replay image platform does not match the signed descriptor: "
            f"expected {environment.platform}"
        )
    actual_variant = record.get("Variant") or None
    if actual_variant != environment.variant:
        raise RuntimeError(
            "local replay image variant does not match the signed descriptor: "
            f"expected {environment.platform}"
        )
    config = record.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if not isinstance(labels, dict):
        raise RuntimeError("local replay image is missing signed runtime identity labels")
    if labels.get(PYTEST_VERSION_LABEL) != environment.pytest_version:
        raise RuntimeError("local replay pytest version does not match the signed descriptor")
    if labels.get(PYTEST_ARTIFACT_LABEL) != environment.pytest_artifact_sha256:
        raise RuntimeError("local replay pytest artifact does not match the signed descriptor")
