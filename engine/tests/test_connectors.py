from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from exhibit_a.connectors import (
    Connector,
    EvidenceKind,
    Freshness,
    LocalTestConnector,
    LocalTestRequest,
    hash_payload,
)
from exhibit_a.engine import EvidenceEngine
from exhibit_a.executor.base import ExecOutcome, ExecSpec, Executor, RepoState
from exhibit_a.executor.docker_exec import DockerExecutor
from exhibit_a.models.case import Case, Mode


class StubExecutor(Executor):
    source_access = "disposable_copy"
    network_access = "host_unrestricted"
    isolation = "host_subprocess"
    credential_access = "ambient_host"

    def __init__(self):
        self.request: tuple[RepoState, ExecSpec] | None = None

    def prepare(self, repo: RepoState) -> None:
        return None

    def run(self, repo: RepoState, spec: ExecSpec) -> ExecOutcome:
        self.request = (repo, spec)
        return ExecOutcome(1, "captured stdout", "captured stderr", duration_s=0.25)


def test_local_test_connector_preserves_raw_evidence_and_records_provenance():
    executor = StubExecutor()
    observed_at = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
    connector = LocalTestConnector(executor, clock=lambda: observed_at)
    repo = RepoState(
        "/secret/checkout",
        "target",
        commit="a" * 40,
        source="https://user:token@example.com/org/repo.git?credential=secret",
    )
    spec = ExecSpec("test_repro.py", "def test_repro(): assert False\n", "pytest -q")

    evidence = connector.collect(LocalTestRequest(repo, spec))

    assert executor.request == (repo, spec)
    assert evidence.payload.log == "captured stdout\ncaptured stderr"
    assert evidence.provenance.connector_id == "local_test_runner"
    assert evidence.provenance.capability is EvidenceKind.TEST_EXECUTION
    assert evidence.provenance.source == "https://example.com/org/repo.git"
    assert evidence.provenance.source_revision == "a" * 40
    assert evidence.provenance.observed_at == "2026-08-13T12:00:00+00:00"
    assert evidence.provenance.request_sha256 == hash_payload(
        {
            "command": "pytest -q",
            "repo_revision": "a" * 40,
            "state": "target",
            "test_code": "def test_repro(): assert False\n",
            "test_path": "test_repro.py",
            "timeout_s": 120,
        }
    )
    assert evidence.provenance.response_sha256 == hash_payload(
        {
            "duration_s": 0.25,
            "exit_code": 1,
            "stderr": "captured stderr",
            "stdout": "captured stdout",
            "timed_out": False,
        }
    )
    assert evidence.provenance.content_sha256 == hash_payload(
        {
            "request_sha256": evidence.provenance.request_sha256,
            "response_sha256": evidence.provenance.response_sha256,
        }
    )
    assert len(evidence.provenance.evidence_id) == 32
    assert evidence.provenance.security.isolation == "host_subprocess"
    assert "/secret/checkout" not in repr(evidence.provenance)
    assert "token" not in repr(evidence.provenance)
    assert "captured stderr" not in repr(evidence.provenance)
    assert "pytest -q" not in repr(evidence.provenance)


def test_local_test_connector_satisfies_typed_contract():
    connector: Connector[LocalTestRequest, ExecOutcome] = LocalTestConnector(StubExecutor())
    assert connector.descriptor.freshness_basis is Freshness.POINT_IN_TIME
    assert connector.descriptor.security.network_access == "host_unrestricted"
    assert connector.descriptor.security.credential_access == "ambient_host"


def test_local_test_connector_rejects_network_enabled_execution():
    connector = LocalTestConnector(StubExecutor())
    repo = RepoState("/checkout", "target")
    spec = ExecSpec("test_repro.py", "assert False\n", "pytest -q", network=True)

    with pytest.raises(ValueError, match="does not permit network-enabled"):
        connector.collect(LocalTestRequest(repo, spec))


def test_docker_connector_reports_process_containment_truthfully():
    security = LocalTestConnector(DockerExecutor()).descriptor.security

    assert security.isolation == "container"
    assert security.network_access == "disabled"
    assert security.credential_access == "none"


def test_engine_rejects_connector_receipt_that_does_not_cover_execution():
    inner = LocalTestConnector(StubExecutor())

    class TamperingConnector:
        descriptor = inner.descriptor

        def collect(self, request: LocalTestRequest):
            output = inner.collect(request)
            return replace(
                output,
                provenance=replace(output.provenance, content_sha256="0" * 64),
            )

    engine = EvidenceEngine(
        generator=object(),  # unused by this focused connector-boundary test
        executor=StubExecutor(),
        test_connector=TamperingConnector(),
    )
    case = Case(id="connector-check", mode=Mode.DETECTIVE)
    repo = RepoState("/checkout", "target")
    spec = ExecSpec("test_repro.py", "assert False\n", "pytest -q")

    with pytest.raises(ValueError, match="does not cover"):
        engine._collect_test(case, repo, spec)
    assert case.evidence_sources == []
