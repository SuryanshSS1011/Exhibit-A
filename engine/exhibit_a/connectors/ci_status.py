"""Read-only CI check-run status connector for GitHub Actions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from ipaddress import ip_address
from typing import Callable
from urllib.parse import quote, urlsplit, urlunsplit

from ..intake.git_checkout import validate_sha
from .base import (
    ConnectorDescriptor,
    ConnectorOutput,
    ConnectorSecurity,
    EvidenceKind,
    EvidenceProvenance,
    Freshness,
    credential_free_source,
    hash_payload,
)

_DEFAULT_API_BASE = "https://api.github.com"
_API_VERSION = "2022-11-28"
_ENVIRONMENT_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_REPOSITORY = re.compile(r"[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}")
_MAX_RESPONSE_BYTES = 1_048_576
_MAX_CHECKS = 250
_MAX_TIMEOUT_S = 120.0
# GitHub's documented vocabulary. An unrecognized value is reported verbatim rather than
# coerced, so a forge-side addition can never be silently mapped onto a known outcome.
_STATUSES = frozenset({"queued", "in_progress", "completed", "waiting", "requested", "pending"})
_CONCLUSIONS = frozenset(
    {
        "success",
        "failure",
        "neutral",
        "cancelled",
        "timed_out",
        "action_required",
        "skipped",
        "stale",
        "startup_failure",
    }
)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class CIStatusRequest:
    repository: str
    revision: str


@dataclass(frozen=True)
class CICheckRun:
    name: str
    status: str
    conclusion: str | None
    started_at: str | None
    completed_at: str | None


@dataclass(frozen=True)
class CIStatus:
    """Raw check-run facts for one commit. Carries no pass/fail judgement."""

    repository: str
    revision: str
    reported_total: int
    checks: tuple[CICheckRun, ...]

    def payload(self) -> dict[str, object]:
        return asdict(self)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _check_runs_url(api_base: str, repository: str, revision: str) -> tuple[str, str]:
    parsed = urlsplit(api_base)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("CI status API base must use http or https")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("CI status API base must not contain credentials, query, or fragment")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("CI status API base requires a hostname")
    if parsed.scheme == "http":
        try:
            is_loopback = ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False
        if not is_loopback:
            raise ValueError("CI status plain HTTP is limited to numeric loopback addresses")
    owner, _, name = repository.partition("/")
    path = f"{parsed.path.rstrip('/')}/repos/{quote(owner)}/{quote(name)}"
    source = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    url = urlunsplit(
        (parsed.scheme, parsed.netloc, f"{path}/commits/{revision}/check-runs", "", "")
    )
    return url, source


class CIStatusConnector:
    """Read check-run status for one commit without granting write or replay authority.

    The connector reports what the forge said and nothing more. It never decides whether
    CI "passed" — aggregating these facts into a claim is the judge's job, not a
    collector's. Credentials are read only from a named environment variable and never
    reach the payload, the provenance, or an error message.
    """

    def __init__(
        self,
        *,
        api_base: str = _DEFAULT_API_BASE,
        token_env: str | None = None,
        timeout_s: float = 10.0,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
        max_checks: int = _MAX_CHECKS,
        clock: Callable[[], datetime] = _utc_now,
    ):
        if token_env is not None and not _ENVIRONMENT_NAME.fullmatch(token_env):
            raise ValueError("CI status token environment name is invalid")
        if not 0 < timeout_s <= _MAX_TIMEOUT_S:
            raise ValueError("CI status timeout is out of range")
        if not 1024 <= max_response_bytes <= _MAX_RESPONSE_BYTES:
            raise ValueError("CI status response limit is out of range")
        if not 1 <= max_checks <= _MAX_CHECKS:
            raise ValueError("CI status check limit is out of range")
        self._api_base = api_base
        self._token_env = token_env
        self._timeout_s = timeout_s
        self._max_response_bytes = max_response_bytes
        self._max_checks = max_checks
        self._clock = clock
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )
        self.descriptor = ConnectorDescriptor(
            id="github_ci_status",
            version="1",
            capabilities=(EvidenceKind.CI_STATUS,),
            freshness_basis=Freshness.POINT_IN_TIME,
            security=ConnectorSecurity(
                source_access="read_only",
                network_access="host_unrestricted",
                isolation="in_process",
                credential_access="ambient_host" if token_env else "none",
            ),
        )

    def collect(self, request: CIStatusRequest) -> ConnectorOutput[CIStatus]:
        if not _REPOSITORY.fullmatch(request.repository):
            raise ValueError("CI status repository must be owner/name")
        validate_sha(request.revision)
        revision = request.revision.lower()
        url, source_url = _check_runs_url(self._api_base, request.repository, revision)
        observed_at = self._clock()
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("connector clock must return a timezone-aware datetime")

        raw = self._get(url)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TypeError("CI status response was not a JSON object")
        checks = _parse_checks(payload.get("check_runs"), self._max_checks)
        status = CIStatus(
            repository=request.repository,
            revision=revision,
            reported_total=_reported_total(payload.get("total_count"), len(checks)),
            checks=checks,
        )

        source = credential_free_source(source_url)
        request_sha256 = hash_payload(
            {"repository": request.repository, "revision": revision, "source": source}
        )
        response_sha256 = hash_payload(status.payload())
        provenance = EvidenceProvenance(
            evidence_id=uuid.uuid4().hex,
            connector_id=self.descriptor.id,
            connector_version=self.descriptor.version,
            capability=EvidenceKind.CI_STATUS,
            source=source,
            source_revision=revision,
            observed_at=observed_at.astimezone(timezone.utc).isoformat(),
            source_updated_at=_latest_completion(checks),
            freshness=self.descriptor.freshness_basis,
            description="Read read-only CI check-run status for one commit",
            request_sha256=request_sha256,
            response_sha256=response_sha256,
            artifact_sha256=hashlib.sha256(raw).hexdigest(),
            content_sha256=hash_payload(
                {"request_sha256": request_sha256, "response_sha256": response_sha256}
            ),
            security=self.descriptor.security,
        )
        return ConnectorOutput(payload=status, provenance=provenance)

    def _get(self, url: str) -> bytes:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
        }
        if self._token_env is not None:
            token = os.environ.get(self._token_env)
            if not token:
                raise RuntimeError(
                    f"CI status token environment variable {self._token_env!r} is not set"
                )
            if token != token.strip() or any(
                ord(character) < 32 or ord(character) == 127 for character in token
            ):
                raise ValueError("CI status token contains invalid characters")
            headers["Authorization"] = f"Bearer {token}"

        http_request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with self._opener.open(http_request, timeout=self._timeout_s) as response:
                raw = response.read(self._max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"CI status endpoint returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"CI status request failed: {exc.reason}") from exc
        if len(raw) > self._max_response_bytes:
            raise ValueError("CI status response exceeded the configured size limit")
        return raw


def _parse_checks(value: object, limit: int) -> tuple[CICheckRun, ...]:
    if not isinstance(value, list):
        raise TypeError("CI status response is missing its check runs")
    if len(value) > limit:
        raise ValueError("CI status response exceeded the configured check limit")
    checks = [_parse_check(item) for item in value]
    # Order is normalized so the evidence digest does not depend on forge response order.
    return tuple(
        sorted(checks, key=lambda run: (run.name, run.started_at or "", run.conclusion or ""))
    )


def _parse_check(item: object) -> CICheckRun:
    if not isinstance(item, dict):
        raise TypeError("CI status check run is invalid")
    name = item.get("name")
    status = item.get("status")
    if not isinstance(name, str) or not name.strip() or len(name) > 256:
        raise ValueError("CI status check run name is invalid")
    if not isinstance(status, str) or status not in _STATUSES:
        raise ValueError("CI status check run status is unrecognized")
    return CICheckRun(
        name=name,
        status=status,
        conclusion=_optional_enum(item.get("conclusion"), _CONCLUSIONS, "conclusion"),
        started_at=_optional_timestamp(item.get("started_at")),
        completed_at=_optional_timestamp(item.get("completed_at")),
    )


def _optional_enum(value: object, allowed: frozenset[str], label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"CI status check run {label} is unrecognized")
    return value


def _optional_timestamp(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("CI status timestamp is invalid")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("CI status timestamp is invalid") from exc
    return value


def _reported_total(value: object, collected: int) -> int:
    if value is None:
        return collected
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("CI status total count is invalid")
    return value


def _latest_completion(checks: tuple[CICheckRun, ...]) -> str | None:
    completions = sorted(run.completed_at for run in checks if run.completed_at)
    return completions[-1] if completions else None
