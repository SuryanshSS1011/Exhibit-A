from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from exhibit_a.connectors import (
    Connector,
    ConnectorOutput,
    EvidenceKind,
    Freshness,
    LocalTestConnector,
    LocalTestRequest,
    collect_validated_local_test,
    hash_payload,
    local_test_digests,
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
    assert evidence.provenance.connector_version == "2"
    assert evidence.provenance.capability is EvidenceKind.TEST_EXECUTION
    assert evidence.provenance.source == "https://example.com/org/repo.git"
    assert evidence.provenance.source_revision == "a" * 40
    assert evidence.provenance.observed_at == "2026-08-13T12:00:00+00:00"
    assert evidence.provenance.request_sha256 == hash_payload(
        {
            "command": "pytest -q",
            "image": None,
            "network": False,
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


def test_validated_collector_rejects_forged_request_provenance():
    inner = LocalTestConnector(StubExecutor())

    class ForgedSourceConnector:
        descriptor = inner.descriptor

        def collect(self, request: LocalTestRequest):
            output = inner.collect(request)
            return replace(
                output,
                provenance=replace(output.provenance, source_revision="b" * 40),
            )

    request = LocalTestRequest(
        RepoState("/checkout", "target", commit="a" * 40),
        ExecSpec("test_repro.py", "assert False\n", "pytest -q"),
    )

    with pytest.raises(ValueError, match="does not match its request"):
        collect_validated_local_test(ForgedSourceConnector(), request)


def test_validated_collector_rejects_descriptor_mutation_during_collection():
    inner = LocalTestConnector(StubExecutor())

    class MutatingDescriptorConnector:
        descriptor = replace(inner.descriptor)

        def collect(self, request: LocalTestRequest):
            object.__setattr__(self.descriptor, "version", "forged")
            return inner.collect(request)

    request = LocalTestRequest(
        RepoState("/checkout", "target"),
        ExecSpec("test_repro.py", "assert False\n", "pytest -q"),
    )

    with pytest.raises(ValueError, match="descriptor changed"):
        collect_validated_local_test(MutatingDescriptorConnector(), request)


def test_validated_collector_returns_snapshot_bound_to_receipt():
    class RetainingExecutor(StubExecutor):
        def __init__(self):
            super().__init__()
            self.outcome = ExecOutcome(1, "captured stdout", "captured stderr", duration_s=0)

        def run(self, repo: RepoState, spec: ExecSpec) -> ExecOutcome:
            self.request = (repo, spec)
            return self.outcome

    executor = RetainingExecutor()
    connector = LocalTestConnector(executor)
    request = LocalTestRequest(
        RepoState("/checkout", "target"),
        ExecSpec("test_repro.py", "assert False\n", "pytest -q"),
    )

    output = collect_validated_local_test(connector, request)
    assert executor.request is not None

    executor.outcome.stdout = "mutated original"

    assert output.payload.stdout == "captured stdout"
    assert (
        output.provenance.response_sha256
        == local_test_digests(request, output.payload)["response_sha256"]
    )


def test_validated_collector_snapshots_connector_owned_provenance():
    inner = LocalTestConnector(StubExecutor())

    class RetainingConnector:
        descriptor = inner.descriptor
        original: ConnectorOutput | None = None

        def collect(self, request: LocalTestRequest):
            self.original = inner.collect(request)
            return self.original

    connector = RetainingConnector()
    request = LocalTestRequest(
        RepoState("/checkout", "target"),
        ExecSpec("test_repro.py", "assert False\n", "pytest -q"),
    )

    output = collect_validated_local_test(connector, request)
    assert connector.original is not None
    original_id = output.provenance.evidence_id
    original_hash = output.provenance.response_sha256

    object.__setattr__(connector.original.provenance, "evidence_id", "0" * 32)
    object.__setattr__(connector.original.provenance, "response_sha256", "0" * 64)
    object.__setattr__(connector.original.provenance.security, "isolation", "unknown")

    assert output.provenance.evidence_id == original_id
    assert output.provenance.response_sha256 == original_hash
    assert output.provenance.security.isolation == "host_subprocess"
    assert (
        output.provenance.response_sha256
        == local_test_digests(request, output.payload)["response_sha256"]
    )


def test_validated_collector_rejects_outcome_subclasses():
    inner = LocalTestConnector(StubExecutor())

    class DerivedOutcome(ExecOutcome):
        pass

    class DerivedConnector:
        descriptor = inner.descriptor

        def collect(self, request: LocalTestRequest):
            output = inner.collect(request)
            payload = DerivedOutcome(
                output.payload.exit_code,
                output.payload.stdout,
                output.payload.stderr,
                duration_s=output.payload.duration_s,
            )
            return ConnectorOutput(payload, output.provenance)

    request = LocalTestRequest(
        RepoState("/checkout", "target"),
        ExecSpec("test_repro.py", "assert False\n", "pytest -q"),
    )

    with pytest.raises(TypeError, match="non-ExecOutcome"):
        collect_validated_local_test(DerivedConnector(), request)


def test_request_digest_binds_network_and_prepared_image():
    repo = RepoState("/checkout", "target", commit="a" * 40)
    plain = LocalTestRequest(repo, ExecSpec("test.py", "assert True\n", "pytest -q"))
    image = LocalTestRequest(
        repo,
        ExecSpec("test.py", "assert True\n", "pytest -q", image="image:prepared"),
    )
    network = LocalTestRequest(
        repo,
        ExecSpec("test.py", "assert True\n", "pytest -q", network=True),
    )
    outcome = ExecOutcome(0, "1 passed", "")

    assert (
        local_test_digests(plain, outcome)["request_sha256"]
        != local_test_digests(image, outcome)["request_sha256"]
    )
    assert (
        local_test_digests(plain, outcome)["request_sha256"]
        != local_test_digests(network, outcome)["request_sha256"]
    )
