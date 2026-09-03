from __future__ import annotations

import json
from datetime import datetime, timezone
from io import BytesIO
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request

import pytest

from exhibit_a.connectors import (
    CIStatusConnector,
    CIStatusRequest,
    GitLabCIStatusConnector,
)

REVISION = "1f9473f8d6940935ec45a41cb518d9038e0bea0e"
REPOSITORY = "example/project"


class FakeResponse(BytesIO):
    def __init__(self, payload: object, *, link: str | None = None):
        super().__init__(json.dumps(payload).encode())
        self.headers = {"Link": link} if link is not None else {}

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class FakeOpener:
    def __init__(self, pages: list[tuple[object, str | None]]):
        self.pages = pages
        self.requests: list[Request] = []

    def open(self, request: Request, *, timeout: float):
        self.requests.append(request)
        payload, link = self.pages.pop(0)
        return FakeResponse(payload, link=link)


def _clock() -> datetime:
    return datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def _status(name: str, status: str, **overrides: object) -> dict[str, object]:
    value = {
        "name": name,
        "sha": REVISION,
        "status": status,
        "started_at": "2026-09-02T11:00:00Z",
        "finished_at": "2026-09-02T11:05:00Z" if status not in {"pending", "running"} else None,
    }
    value.update(overrides)
    return value


def _connector(pages: list[tuple[object, str | None]], **kwargs: object) -> GitLabCIStatusConnector:
    connector = GitLabCIStatusConnector(clock=_clock, **kwargs)
    connector._opener = FakeOpener(pages)
    return connector


def test_gitlab_maps_only_shared_status_semantics_and_preserves_unknowns():
    connector = _connector(
        [
            (
                [
                    _status("web", "success"),
                    _status("engine", "failed"),
                    _status("deploy", "manual", finished_at=None),
                ],
                None,
            )
        ]
    )

    output = connector.collect(CIStatusRequest(REPOSITORY, REVISION))

    assert [(check.name, check.status, check.conclusion) for check in output.payload.checks] == [
        ("deploy", "gitlab:manual", None),
        ("engine", "completed", "failure"),
        ("web", "completed", "success"),
    ]
    assert output.payload.reported_total == 3
    assert output.provenance.connector_id == "gitlab_ci_status"
    assert output.provenance.source == "https://gitlab.com/api/v4/projects/example%2Fproject"
    assert output.provenance.source_updated_at == "2026-09-02T11:05:00Z"


def test_gitlab_exhausts_validated_pagination_and_hashes_every_raw_page():
    next_url = (
        "https://gitlab.com/api/v4/projects/example%2Fproject/repository/commits/"
        f"{REVISION}/statuses?all=false&order_by=id&page=2&per_page=100&sort=asc"
    )
    connector = _connector(
        [
            ([_status("engine", "success")], f'<{next_url}>; rel="next"'),
            ([_status("web", "running", finished_at=None)], None),
        ]
    )

    output = connector.collect(CIStatusRequest(REPOSITORY, REVISION))

    assert output.payload.reported_total == 2
    assert [request.full_url for request in connector._opener.requests] == [
        (
            "https://gitlab.com/api/v4/projects/example%2Fproject/repository/commits/"
            f"{REVISION}/statuses?all=false&order_by=id&page=1&per_page=100&sort=asc"
        ),
        next_url,
    ]
    assert len(output.provenance.artifact_sha256) == 64


def test_gitlab_request_is_read_only_url_encoded_and_uses_named_token(monkeypatch):
    monkeypatch.setenv("EXHIBIT_A_GITLAB_TOKEN", "glpat-secret")
    connector = _connector([([], None)], token_env="EXHIBIT_A_GITLAB_TOKEN")

    connector.collect(CIStatusRequest(REPOSITORY, REVISION))

    request = connector._opener.requests[0]
    assert request.get_method() == "GET"
    assert request.get_header("Private-token") == "glpat-secret"
    assert "example%2Fproject" in request.full_url
    assert connector.descriptor.security.credential_access == "ambient_host"


def test_gitlab_requires_the_named_token_without_recording_it(monkeypatch):
    monkeypatch.delenv("EXHIBIT_A_GITLAB_TOKEN", raising=False)
    connector = _connector([([], None)], token_env="EXHIBIT_A_GITLAB_TOKEN")

    with pytest.raises(RuntimeError, match="EXHIBIT_A_GITLAB_TOKEN.*not set") as error:
        connector.collect(CIStatusRequest(REPOSITORY, REVISION))

    assert "glpat" not in str(error.value)


def test_gitlab_disables_ambient_proxies_and_redirects(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:8080")

    connector = GitLabCIStatusConnector()

    assert [
        handler for handler in connector._opener.handlers if isinstance(handler, ProxyHandler)
    ] == []
    redirects = [
        handler
        for handler in connector._opener.handlers
        if isinstance(handler, HTTPRedirectHandler)
    ]
    assert len(redirects) == 1
    assert redirects[0].redirect_request(None, None, 302, "Found", {}, "https://evil") is None


@pytest.mark.parametrize(
    "api_base",
    [
        "http://gitlab.com/api/v4",
        "https://user:secret@gitlab.com/api/v4",
        "https://gitlab.com/api/v4?token=x",
        "ftp://gitlab.com/api/v4",
    ],
)
def test_gitlab_rejects_unsafe_api_bases(api_base: str):
    connector = _connector([([], None)], api_base=api_base)

    with pytest.raises(ValueError):
        connector.collect(CIStatusRequest(REPOSITORY, REVISION))


def test_gitlab_allows_plain_http_only_for_numeric_loopback():
    connector = _connector([([], None)], api_base="http://127.0.0.1:8080/api/v4")

    output = connector.collect(CIStatusRequest(REPOSITORY, REVISION))

    assert output.provenance.source == ("http://127.0.0.1:8080/api/v4/projects/example%2Fproject")


@pytest.mark.parametrize(
    "link",
    [
        (
            "<https://evil.example/api/v4/projects/example%2Fproject/repository/commits/"
            f"{REVISION}/statuses?all=false&order_by=id&page=2&per_page=100&sort=asc>; rel=next"
        ),
        (
            "<https://gitlab.com/api/v4/projects/example%2Fproject/repository/commits/"
            f"{REVISION}/statuses?all=false&order_by=id&page=2&per_page=100&sort=asc&token=x>; rel=next"
        ),
        (
            "<https://gitlab.com/api/v4/projects/example%2Fproject/repository/commits/"
            f"{REVISION}/statuses?all=false&order_by=id&page=3&per_page=100&sort=asc>; rel=next"
        ),
    ],
)
def test_gitlab_pagination_cannot_escape_or_add_parameters(link: str):
    connector = _connector([([_status("engine", "success")], link)])

    with pytest.raises(ValueError, match="pagination"):
        connector.collect(CIStatusRequest(REPOSITORY, REVISION))


def test_gitlab_rejects_wrong_revision_and_check_count_overflow():
    wrong_revision = _connector([([_status("engine", "success", sha="f" * 40)], None)])
    with pytest.raises(ValueError, match="revision"):
        wrong_revision.collect(CIStatusRequest(REPOSITORY, REVISION))

    too_many = _connector(
        [([_status("one", "success"), _status("two", "success")], None)],
        max_checks=1,
    )
    with pytest.raises(ValueError, match="check limit"):
        too_many.collect(CIStatusRequest(REPOSITORY, REVISION))


def test_gitlab_enforces_the_aggregate_response_limit():
    connector = _connector(
        [([_status("engine", "success", padding="x" * 4096)], None)],
        max_response_bytes=1024,
    )

    with pytest.raises(ValueError, match="size limit"):
        connector.collect(CIStatusRequest(REPOSITORY, REVISION))


def test_github_and_gitlab_normalize_overlapping_facts_identically():
    github = CIStatusConnector(clock=_clock)
    github._opener = FakeOpener(
        [
            (
                {
                    "total_count": 2,
                    "check_runs": [
                        {
                            "name": "engine",
                            "status": "completed",
                            "conclusion": "success",
                            "started_at": "2026-09-02T11:00:00Z",
                            "completed_at": "2026-09-02T11:05:00Z",
                        },
                        {
                            "name": "web",
                            "status": "completed",
                            "conclusion": "failure",
                            "started_at": "2026-09-02T11:00:00Z",
                            "completed_at": "2026-09-02T11:05:00Z",
                        },
                    ],
                },
                None,
            )
        ]
    )
    gitlab = _connector([([_status("engine", "success"), _status("web", "failed")], None)])

    github_status = github.collect(CIStatusRequest(REPOSITORY, REVISION)).payload
    gitlab_status = gitlab.collect(CIStatusRequest(REPOSITORY, REVISION)).payload

    assert github_status == gitlab_status
