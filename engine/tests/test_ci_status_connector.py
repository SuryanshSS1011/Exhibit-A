from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timezone
from io import BytesIO
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request

import pytest

from exhibit_a.connectors import (
    CIStatusConnector,
    CIStatusRequest,
    EvidenceKind,
    Freshness,
)

REVISION = "1f9473f8d6940935ec45a41cb518d9038e0bea0e"
REPOSITORY = "SuryanshSS1011/Exhibit-A"


class FakeResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class FakeOpener:
    def __init__(self, payload: object):
        self.payload = payload
        self.request: Request | None = None
        self.timeout: float | None = None

    def open(self, request: Request, *, timeout: float):
        self.request = request
        self.timeout = timeout
        return FakeResponse(json.dumps(self.payload).encode())


class FailingOpener:
    def __init__(self, error: Exception):
        self.error = error

    def open(self, request: Request, *, timeout: float):
        raise self.error


def _clock() -> datetime:
    return datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)


def _payload(**overrides: object) -> dict:
    payload = {
        "total_count": 2,
        "check_runs": [
            {
                "name": "web",
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-08-18T11:00:00Z",
                "completed_at": "2026-08-18T11:02:00Z",
            },
            {
                "name": "engine",
                "status": "completed",
                "conclusion": "failure",
                "started_at": "2026-08-18T11:00:00Z",
                "completed_at": "2026-08-18T11:05:00Z",
            },
        ],
    }
    payload.update(overrides)
    return payload


def _connector(payload: object, **kwargs) -> CIStatusConnector:
    connector = CIStatusConnector(clock=_clock, **kwargs)
    connector._opener = FakeOpener(payload)
    return connector


def test_collect_reports_raw_check_runs_with_hash_linked_provenance():
    connector = _connector(_payload())

    output = connector.collect(CIStatusRequest(REPOSITORY, REVISION))

    assert output.payload.repository == REPOSITORY
    assert output.payload.revision == REVISION
    assert output.payload.reported_total == 2
    # Normalized to name order so the digest does not depend on forge response order.
    assert [(run.name, run.conclusion) for run in output.payload.checks] == [
        ("engine", "failure"),
        ("web", "success"),
    ]

    provenance = output.provenance
    assert provenance.capability is EvidenceKind.CI_STATUS
    assert provenance.freshness is Freshness.POINT_IN_TIME
    assert provenance.source == "https://api.github.com/repos/SuryanshSS1011/Exhibit-A"
    assert provenance.source_revision == REVISION
    assert provenance.observed_at == "2026-08-18T12:00:00+00:00"
    assert provenance.source_updated_at == "2026-08-18T11:05:00Z"
    assert provenance.description == "Read read-only CI check-run status for one commit"
    assert len(provenance.artifact_sha256) == 64


def test_collect_requests_a_read_only_url_without_redirects_or_proxies():
    connector = _connector(_payload())

    connector.collect(CIStatusRequest(REPOSITORY, REVISION))

    request = connector._opener.request
    assert request.get_method() == "GET"
    assert request.full_url == (
        f"https://api.github.com/repos/SuryanshSS1011/Exhibit-A/commits/{REVISION}/check-runs"
    )
    assert request.get_header("Authorization") is None


def test_ambient_proxies_and_redirects_are_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:8080")

    connector = CIStatusConnector()

    assert [h for h in connector._opener.handlers if isinstance(h, ProxyHandler)] == []
    redirects = [h for h in connector._opener.handlers if isinstance(h, HTTPRedirectHandler)]
    assert len(redirects) == 1
    assert redirects[0].redirect_request(None, None, 302, "Found", {}, "https://evil") is None


def test_collect_sends_the_environment_token_and_keeps_it_out_of_the_evidence(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("EXHIBIT_A_CI_TOKEN", "ghp_SecretCredential123")
    connector = _connector(_payload(), token_env="EXHIBIT_A_CI_TOKEN")

    output = connector.collect(CIStatusRequest(REPOSITORY, REVISION))

    assert connector._opener.request.get_header("Authorization") == "Bearer ghp_SecretCredential123"
    assert connector.descriptor.security.credential_access == "ambient_host"
    recorded = json.dumps(output.payload.payload()) + json.dumps(
        vars(output.provenance), default=str
    )
    assert "ghp_SecretCredential123" not in recorded


def test_collect_fails_when_the_named_token_is_absent(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("EXHIBIT_A_CI_TOKEN", raising=False)
    connector = _connector(_payload(), token_env="EXHIBIT_A_CI_TOKEN")

    with pytest.raises(RuntimeError, match="is not set"):
        connector.collect(CIStatusRequest(REPOSITORY, REVISION))


def test_unauthenticated_connector_declares_no_credential_access():
    assert CIStatusConnector().descriptor.security.credential_access == "none"


@pytest.mark.parametrize(
    "api_base",
    [
        "http://api.github.com",
        "https://user:secret@api.github.com",
        "https://api.github.com?token=x",
        "ftp://api.github.com",
    ],
)
def test_rejects_unsafe_api_bases(api_base: str):
    connector = _connector(_payload(), api_base=api_base)

    with pytest.raises(ValueError):
        connector.collect(CIStatusRequest(REPOSITORY, REVISION))


def test_allows_plain_http_only_for_numeric_loopback():
    connector = _connector(_payload(), api_base="http://127.0.0.1:8080")

    output = connector.collect(CIStatusRequest(REPOSITORY, REVISION))

    assert output.provenance.source == "http://127.0.0.1:8080/repos/SuryanshSS1011/Exhibit-A"


@pytest.mark.parametrize(
    "repository",
    ["Exhibit-A", "owner/name/extra", "owner/../etc", "owner/na me", ""],
)
def test_rejects_malformed_repositories(repository: str):
    connector = _connector(_payload())

    with pytest.raises(ValueError):
        connector.collect(CIStatusRequest(repository, REVISION))


def test_rejects_a_malformed_revision():
    connector = _connector(_payload())

    with pytest.raises(ValueError):
        connector.collect(CIStatusRequest(REPOSITORY, "not-a-sha"))


@pytest.mark.parametrize(
    "payload",
    [
        {"check_runs": [{"name": "engine", "status": "exploded"}]},
        {"check_runs": [{"name": "engine", "status": "completed", "conclusion": "probably"}]},
        {"check_runs": [{"name": "", "status": "completed"}]},
        {"check_runs": [{"name": "engine", "status": "completed", "started_at": "yesterday"}]},
        {"check_runs": "not-a-list"},
        {"check_runs": [], "total_count": -1},
    ],
)
def test_rejects_unrecognized_forge_payloads(payload: dict):
    connector = _connector(payload)

    with pytest.raises((ValueError, TypeError)):
        connector.collect(CIStatusRequest(REPOSITORY, REVISION))


def test_rejects_more_checks_than_the_configured_limit():
    runs = [{"name": f"job-{index}", "status": "queued"} for index in range(3)]
    connector = _connector({"check_runs": runs}, max_checks=2)

    with pytest.raises(ValueError, match="check limit"):
        connector.collect(CIStatusRequest(REPOSITORY, REVISION))


def test_rejects_a_response_larger_than_the_configured_limit():
    connector = _connector(_payload(), max_response_bytes=1024)
    connector._opener = FakeOpener({"check_runs": [], "padding": "x" * 4096})

    with pytest.raises(ValueError, match="size limit"):
        connector.collect(CIStatusRequest(REPOSITORY, REVISION))


def test_reports_transport_failures_without_leaking_the_url():
    connector = _connector(_payload())
    connector._opener = FailingOpener(urllib.error.URLError("name resolution failed"))

    with pytest.raises(RuntimeError, match="CI status request failed"):
        connector.collect(CIStatusRequest(REPOSITORY, REVISION))


def test_reports_http_status_failures():
    connector = _connector(_payload())
    connector._opener = FailingOpener(
        urllib.error.HTTPError("https://api.github.com", 404, "Not Found", {}, None)
    )

    with pytest.raises(RuntimeError, match="HTTP 404"):
        connector.collect(CIStatusRequest(REPOSITORY, REVISION))


def test_identical_responses_produce_identical_evidence_digests():
    first = _connector(_payload()).collect(CIStatusRequest(REPOSITORY, REVISION))
    second = _connector(_payload()).collect(CIStatusRequest(REPOSITORY, REVISION))

    assert first.provenance.content_sha256 == second.provenance.content_sha256
    assert first.provenance.response_sha256 == second.provenance.response_sha256
    # evidence_id is per-observation and must not be reused across collections.
    assert first.provenance.evidence_id != second.provenance.evidence_id


def test_connector_rejects_invalid_construction():
    with pytest.raises(ValueError):
        CIStatusConnector(token_env="lower_case_env")
    with pytest.raises(ValueError):
        CIStatusConnector(timeout_s=0)
    with pytest.raises(ValueError):
        CIStatusConnector(max_checks=0)
