from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

import exhibit_a.verdict as verdict_package
from exhibit_a.engine import EngineConfig, EvidenceEngine
from exhibit_a.executor.base import ExecSpec, Executor, RepoState
from exhibit_a.executor.local_exec import LocalExecutor
from exhibit_a.hypothesis.generator import Candidate, Claim
from exhibit_a.models.case import Case, Mode, ProposalRun, Verdict
from exhibit_a.providers import (
    ProviderResponse,
    RuntimeModel,
    TokenUsage,
    UnknownModelIdentity,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


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


def test_engine_records_legitimate_model_name_containing_unknown(tmp_path: Path):
    class NamedTelemetryGenerator(RefiningTelemetryGenerator):
        def propose(self, claim: Claim, max_hypotheses: int = 3) -> list[Candidate]:
            self.responses.append(
                ProviderResponse(
                    output={},
                    runtime_model=RuntimeModel(
                        provider="test-provider",
                        requested_model="alias",
                        confirmed_model="company/unknown-7b",
                        confirmed_version=UnknownModelIdentity.UNVERIFIED_BACKEND,
                    ),
                )
            )
            return []

    engine = EvidenceEngine(
        NamedTelemetryGenerator(),
        NoRunExecutor(),
        EngineConfig(check_existing_suite=False),
    )

    case = engine.investigate(Claim("claim", str(tmp_path)))

    assert case.proposal_runs[0].confirmed_model == "company/unknown-7b"


FLIP_TEST = (
    "from slicer import last_n\n\n"
    "def test_last_n_keeps_final_element():\n"
    "    assert last_n([1, 2, 3, 4], 2) == [3, 4]\n"
)


class NamedProviderGenerator:
    """Proposes one fixed candidate while reporting a distinct provider identity."""

    def __init__(self, provider: str, model: str):
        self.provider = provider
        self.model = model
        self.responses: list[ProviderResponse] = []

    def propose(self, claim: Claim, max_hypotheses: int = 3) -> list[Candidate]:
        self.responses.append(
            ProviderResponse(
                output={},
                runtime_model=RuntimeModel(
                    provider=self.provider,
                    requested_model=self.model,
                    confirmed_model=self.model,
                    confirmed_version="1.0",
                ),
                usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            )
        )
        return [
            Candidate(
                hypothesis="last_n drops the final element (off-by-one slice)",
                test_path="test_repro.py",
                test_code=FLIP_TEST,
                run_command=f"{sys.executable} -m pytest -x -q test_repro.py",
                expected_signature="AssertionError",
            )
        ]

    def refine(self, claim: Claim, feedback) -> None:
        return None

    def drain_provider_responses(self) -> list[ProviderResponse]:
        responses = self.responses
        self.responses = []
        return responses


def _investigate_as(provider: str, model: str) -> Case:
    engine = EvidenceEngine(
        NamedProviderGenerator(provider, model),
        LocalExecutor(),
        EngineConfig(
            reruns=3,
            run_command=f"{sys.executable} -m pytest -x -q test_repro.py",
            minimize_proven=False,
            score_evidence_strength=False,
        ),
    )
    return engine.investigate(
        Claim(
            text="last_n drops the last row",
            repo_path=str(FIXTURES / "buggy_slice"),
            expected_signature="AssertionError",
        ),
        mode=Mode.DETECTIVE,
        target=RepoState(path=str(FIXTURES / "buggy_slice"), label="target"),
        base=RepoState(path=str(FIXTURES / "fixed_slice"), label="base"),
    )


def _judged_facts(case: Case) -> tuple:
    return (
        case.verdict,
        case.truth.execution,
        case.truth.goal,
        case.truth.release,
        case.evidence.deterministic,
        case.evidence.fail_signature,
        case.evidence.reruns,
        tuple((run.state, run.exit_code, run.passed, run.signature) for run in case.evidence.runs),
        tuple(source.description for source in case.evidence_sources),
        case.test_file.code if case.test_file else None,
    )


def test_verdict_is_identical_for_the_same_candidate_from_different_providers():
    anthropic_case = _investigate_as("anthropic", "claude-model")
    ollama_case = _investigate_as("ollama", "local-model")

    assert anthropic_case.verdict is Verdict.VERIFIED, anthropic_case.silence_reason
    assert _judged_facts(anthropic_case) == _judged_facts(ollama_case)

    # The recorded provider identity is the only thing that may differ.
    assert [run.provider for run in anthropic_case.proposal_runs] == ["anthropic"]
    assert [run.provider for run in ollama_case.proposal_runs] == ["ollama"]


def test_verdict_modules_never_import_provider_code():
    imports_providers = re.compile(r"^\s*(?:from|import)\s+[\w.]*providers", re.MULTILINE)
    modules = sorted(Path(verdict_package.__file__).parent.glob("*.py"))

    assert modules
    offenders = [module.name for module in modules if imports_providers.search(module.read_text())]
    assert not offenders, f"the judge must stay provider-blind: {offenders}"
