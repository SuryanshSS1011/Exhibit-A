from __future__ import annotations

from pathlib import Path

import pytest

from exhibit_a.engine import EngineConfig, EvidenceEngine
from exhibit_a.executor.base import ExecSpec, Executor, RepoState
from exhibit_a.hypothesis.generator import Candidate, Claim
from exhibit_a.models.case import ProposalRun
from exhibit_a.providers import (
    ProviderResponse,
    RuntimeModel,
    TokenUsage,
    UnknownModelIdentity,
)


class NoRunExecutor(Executor):
    def prepare(self, repo: RepoState) -> str | None:
        return None

    def run(self, repo: RepoState, spec: ExecSpec):
        raise AssertionError("provider evidence test must not execute a candidate")


def _response(model: str, latency_ms: float) -> ProviderResponse:
    return ProviderResponse(
        output={},
        runtime_model=RuntimeModel(
            provider="test-provider",
            requested_model=model,
            confirmed_model=UnknownModelIdentity.NO_TELEMETRY,
            confirmed_version=UnknownModelIdentity.NO_TELEMETRY,
        ),
        usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        cost_usd=0.002,
        latency_ms=latency_ms,
    )


class RefiningTelemetryGenerator:
    def __init__(self):
        self.responses: list[ProviderResponse] = []

    def propose(self, claim: Claim, max_hypotheses: int = 3) -> list[Candidate]:
        self.responses.append(_response("requested-propose", 12.5))
        return [
            Candidate(
                hypothesis="unsafe candidate rejected before execution",
                test_path="test_repro.py",
                test_code="def test_repro():\n    assert False\n",
                run_command="pytest -q test_repro.py; touch source.py",
            )
        ]

    def refine(self, claim: Claim, feedback) -> None:
        self.responses.append(_response("requested-refine", 7.5))

    def drain_provider_responses(self) -> list[ProviderResponse]:
        responses = self.responses
        self.responses = []
        return responses


def test_case_records_confirmed_or_explicitly_unknown_identity_for_each_model_response(
    tmp_path: Path,
):
    generator = RefiningTelemetryGenerator()
    engine = EvidenceEngine(
        generator,
        NoRunExecutor(),
        EngineConfig(check_existing_suite=False, max_refine=1),
    )

    case = engine.investigate(Claim("claim", str(tmp_path)))

    assert [run.operation for run in case.proposal_runs] == ["propose", "refine"]
    assert [run.requested_model for run in case.proposal_runs] == [
        "requested-propose",
        "requested-refine",
    ]
    assert all(run.confirmed_model == "unknown_no_telemetry" for run in case.proposal_runs)
    assert case.proposal_runs[0].total_tokens == 15
    assert case.proposal_runs[0].cost_usd == 0.002
    assert case.proposal_runs[0].latency_ms == 12.5
    assert len(case.proposal_runs[0].output_sha256) == 64
    assert case.to_dict()["proposal_runs"][0]["confirmed_model"] == "unknown_no_telemetry"


class StaleAmbientResponseGenerator(RefiningTelemetryGenerator):
    def refine(self, claim: Claim, feedback) -> None:
        self.last_response = _response("stale-ambient-value", 99)


def test_engine_does_not_record_ambient_last_response_as_a_new_call(tmp_path: Path):
    engine = EvidenceEngine(
        StaleAmbientResponseGenerator(),
        NoRunExecutor(),
        EngineConfig(check_existing_suite=False, max_refine=1),
    )

    case = engine.investigate(Claim("claim", str(tmp_path)))

    assert [run.operation for run in case.proposal_runs] == ["propose"]


def test_engine_discards_queued_responses_before_starting_a_case(tmp_path: Path):
    generator = RefiningTelemetryGenerator()
    generator.responses.append(_response("old-case", 100))
    engine = EvidenceEngine(
        generator,
        NoRunExecutor(),
        EngineConfig(check_existing_suite=False, max_refine=1),
    )

    case = engine.investigate(Claim("claim", str(tmp_path)))

    assert [run.requested_model for run in case.proposal_runs] == [
        "requested-propose",
        "requested-refine",
    ]


@pytest.mark.parametrize("count", [True, 1.5, -1])
def test_proposal_run_rejects_invalid_count_telemetry(count):
    with pytest.raises(ValueError, match="non-negative integer"):
        ProposalRun(
            operation="propose",
            provider="provider",
            requested_model="requested",
            confirmed_model="unknown_no_telemetry",
            confirmed_version="unknown_no_telemetry",
            output_sha256="0" * 64,
            input_tokens=count,
        )


def test_proposal_run_rejects_ambiguous_unknown_identity():
    with pytest.raises(ValueError, match="unsupported unknown"):
        ProposalRun(
            operation="propose",
            provider="provider",
            requested_model="requested",
            confirmed_model="unknown",
            confirmed_version="unknown_no_telemetry",
            output_sha256="0" * 64,
        )
