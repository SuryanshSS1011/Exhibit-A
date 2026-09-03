"""Read-only GitHub and GitLab CI status connectors."""

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
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit

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
_DEFAULT_GITLAB_API_BASE = "https://gitlab.com/api/v4"
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
_GITLAB_STATUS_MAP = {
    "pending": ("queued", None),
    "running": ("in_progress", None),
    "success": ("completed", "success"),
    "failed": ("completed", "failure"),
    "canceled": ("completed", "cancelled"),
    "skipped": ("completed", "skipped"),
}
_GITLAB_PER_PAGE = 100
_MAX_GITLAB_PAGES = 32


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


class GitLabCIStatusConnector:
    """Read commit statuses from GitLab and normalize only shared CI facts."""

    def __init__(
        self,
        *,
        api_base: str = _DEFAULT_GITLAB_API_BASE,
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
            id="gitlab_ci_status",
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
        url, source_url = _gitlab_status_url(self._api_base, request.repository, revision)
        observed_at = self._clock()
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("connector clock must return a timezone-aware datetime")

        items: list[object] = []
        raw_pages: list[bytes] = []
        seen_urls: set[str] = set()
        next_url: str | None = url
        while next_url is not None:
            if next_url in seen_urls or len(seen_urls) >= _MAX_GITLAB_PAGES:
                raise ValueError("GitLab CI status pagination is invalid or exceeds its limit")
            seen_urls.add(next_url)
            raw, link = self._get_page(
                next_url,
                remaining_bytes=self._max_response_bytes - sum(map(len, raw_pages)),
            )
            raw_pages.append(raw)
            page = json.loads(raw)
            if not isinstance(page, list):
                raise TypeError("GitLab CI status response was not a JSON array")
            if len(items) + len(page) > self._max_checks:
                raise ValueError("CI status response exceeded the configured check limit")
            items.extend(page)
            next_url = _gitlab_next_url(link, initial_url=url, current_url=next_url)
            if next_url is not None and not page:
                raise ValueError("GitLab CI status pagination returned an empty intermediate page")

        checks = tuple(
            sorted(
                (_parse_gitlab_check(item, revision) for item in items),
                key=lambda run: (run.name, run.started_at or "", run.conclusion or ""),
            )
        )
        status = CIStatus(request.repository, revision, len(checks), checks)
        source = credential_free_source(source_url)
        request_sha256 = hash_payload(
            {"repository": request.repository, "revision": revision, "source": source}
        )
        response_sha256 = hash_payload(status.payload())
        artifact = hashlib.sha256()
        for raw in raw_pages:
            artifact.update(len(raw).to_bytes(8, "big"))
            artifact.update(raw)
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
            description="Read read-only GitLab commit status for one commit",
            request_sha256=request_sha256,
            response_sha256=response_sha256,
            artifact_sha256=artifact.hexdigest(),
            content_sha256=hash_payload(
                {"request_sha256": request_sha256, "response_sha256": response_sha256}
            ),
            security=self.descriptor.security,
        )
        return ConnectorOutput(payload=status, provenance=provenance)

    def _get_page(self, url: str, *, remaining_bytes: int) -> tuple[bytes, str | None]:
        headers = {"Accept": "application/json"}
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
            headers["PRIVATE-TOKEN"] = token
        http_request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with self._opener.open(http_request, timeout=self._timeout_s) as response:
                raw = response.read(max(remaining_bytes, 0) + 1)
                link = response.headers.get("Link")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"CI status endpoint returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"CI status request failed: {exc.reason}") from exc
        if len(raw) > remaining_bytes:
            raise ValueError("CI status response exceeded the configured size limit")
        return raw, link


def _gitlab_status_url(api_base: str, repository: str, revision: str) -> tuple[str, str]:
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
    project = quote(repository, safe="")
    source_path = f"{parsed.path.rstrip('/')}/projects/{project}"
    status_path = f"{source_path}/repository/commits/{revision}/statuses"
    query = urlencode(
        {
            "all": "false",
            "order_by": "id",
            "page": "1",
            "per_page": str(_GITLAB_PER_PAGE),
            "sort": "asc",
        }
    )
    source = urlunsplit((parsed.scheme, parsed.netloc, source_path, "", ""))
    return urlunsplit((parsed.scheme, parsed.netloc, status_path, query, "")), source


def _gitlab_next_url(
    link: str | None,
    *,
    initial_url: str,
    current_url: str,
) -> str | None:
    if not link:
        return None
    if not isinstance(link, str):
        raise ValueError("GitLab CI status pagination link is invalid")
    candidates = []
    for section in link.split(","):
        if re.search(r"\brel\s*=\s*[\"']?next[\"']?", section, re.IGNORECASE):
            match = re.search(r"<([^<>]+)>", section)
            if match is None:
                raise ValueError("GitLab CI status pagination link is invalid")
            candidates.append(match.group(1))
    if not candidates:
        return None
    if len(candidates) != 1:
        raise ValueError("GitLab CI status pagination link is ambiguous")
    initial = urlsplit(initial_url)
    candidate = urlsplit(candidates[0])
    if (
        candidate.scheme != initial.scheme
        or candidate.netloc != initial.netloc
        or candidate.path != initial.path
        or candidate.username
        or candidate.password
        or candidate.fragment
    ):
        raise ValueError("GitLab CI status pagination escaped the original endpoint")
    try:
        query = parse_qs(candidate.query, strict_parsing=True)
        current_query = parse_qs(urlsplit(current_url).query, strict_parsing=True)
    except ValueError as exc:
        raise ValueError("GitLab CI status pagination query is invalid") from exc
    expected = {"all", "order_by", "page", "per_page", "sort"}
    if set(query) != expected or any(len(values) != 1 for values in query.values()):
        raise ValueError("GitLab CI status pagination query is invalid")
    if (
        query["all"] != ["false"]
        or query["order_by"] != ["id"]
        or query["per_page"] != [str(_GITLAB_PER_PAGE)]
        or query["sort"] != ["asc"]
        or not query["page"][0].isdigit()
        or not current_query.get("page", [""])[0].isdigit()
        or int(query["page"][0]) != int(current_query["page"][0]) + 1
    ):
        raise ValueError("GitLab CI status pagination query is invalid")
    return candidates[0]


def _parse_gitlab_check(item: object, revision: str) -> CICheckRun:
    if not isinstance(item, dict):
        raise TypeError("GitLab CI status is invalid")
    name = item.get("name")
    raw_status = item.get("status")
    sha = item.get("sha")
    if not isinstance(name, str) or not name.strip() or name != name.strip() or len(name) > 256:
        raise ValueError("CI status check run name is invalid")
    if not isinstance(sha, str) or sha.lower() != revision:
        raise ValueError("GitLab CI status revision does not match its request")
    if not isinstance(raw_status, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,55}", raw_status):
        raise ValueError("GitLab CI status value is invalid")
    status, conclusion = _GITLAB_STATUS_MAP.get(raw_status, (f"gitlab:{raw_status}", None))
    return CICheckRun(
        name=name,
        status=status,
        conclusion=conclusion,
        started_at=_optional_timestamp(item.get("started_at")),
        completed_at=_optional_timestamp(item.get("finished_at")),
    )


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
