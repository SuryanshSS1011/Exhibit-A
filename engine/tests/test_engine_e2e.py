"""End-to-end tests for the Evidence Engine against the built-in fixtures.

These run with the LocalExecutor (no Docker) and small hand-written generators, so
they exercise the executor + flip-check + verdict wiring without a live model.
They prove the two verdicts the whole product hinges on:
  - PROVEN: a real fail-to-pass test that fails on buggy, passes on fixed.
  - INSUFFICIENT_EVIDENCE: silence when no admissible flip is found.
"""

from __future__ import annotations

import sys
from pathlib import Path

from exhibit_a import EngineConfig, EvidenceEngine
from exhibit_a.executor.base import EnvironmentSetupError, ExecSpec, Executor, RepoState
from exhibit_a.executor.local_exec import LocalExecutor
from exhibit_a.hypothesis.generator import Candidate, Claim, Feedback, StubGenerator
from exhibit_a.hypothesis.intent import IntentAssessment
from exhibit_a.models.case import Disposition, IntentJudgment, Mode, Verdict

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

# A test that fails on the buggy slicer (drops last element) and passes on the fix.
FLIP_TEST = (
    "from slicer import last_n\n\n"
    "def test_last_n_keeps_final_element():\n"
    "    assert last_n([1, 2, 3, 4], 2) == [3, 4]\n"
)

INVENTORY_FLIP_TEST = (
    "from inventory import stock_for\n\n"
    "def test_unknown_sku_has_zero_stock():\n"
    "    assert stock_for([{'sku': 'known', 'quantity': 4}], 'missing') == 0\n"
)


class OneShotGenerator:
    """Emits a single, real fail-to-pass candidate for the slicer fixture."""

    def propose(self, claim: Claim, max_hypotheses: int = 3) -> list[Candidate]:
        return [
            Candidate(
                hypothesis="last_n drops the final element (off-by-one slice)",
                test_path="test_repro.py",
                test_code=FLIP_TEST,
                run_command=f"{sys.executable} -m pytest -x -q test_repro.py",
                expected_signature="AssertionError",
            )
        ]

    def refine(self, claim, feedback: Feedback):
        return None


class TwoShotGenerator(OneShotGenerator):
    def propose(self, claim: Claim, max_hypotheses: int = 3) -> list[Candidate]:
        first = super().propose(claim, max_hypotheses)[0]
        second = Candidate(
            hypothesis="this admissible candidate must never run after proof",
            test_path="test_second.py",
            test_code=FLIP_TEST,
            run_command=f"{sys.executable} -m pytest -x -q test_second.py",
            expected_signature="AssertionError",
        )
        return [first, second]


class InventoryGenerator:
    def propose(self, claim: Claim, max_hypotheses: int = 3) -> list[Candidate]:
        return [
            Candidate(
                hypothesis="stock_for indexes a missing SKU instead of returning zero",
                test_path="test_inventory_repro.py",
                test_code=INVENTORY_FLIP_TEST,
                run_command=f"{sys.executable} -m pytest -x -q test_inventory_repro.py",
                expected_signature="KeyError",
            )
        ]

    def refine(self, claim: Claim, feedback: Feedback):
        return None


class StaticIntentJudge:
    def __init__(self, judgment: IntentJudgment):
        self.judgment = judgment
        self.calls = 0

    def assess(self, repo_path: str, delta: str, context: str) -> IntentAssessment:
        self.calls += 1
        return IntentAssessment(
            self.judgment,
            "PR context says the refactor should preserve behavior.",
            "test-intent-model",
            "Preserve inventory lookup behavior during the refactor.",
            ("pr_description",),
        )


def _cfg() -> EngineConfig:
    # Fewer reruns keeps the test fast; still exercises the determinism gate.
    return EngineConfig(reruns=3, run_command=f"{sys.executable} -m pytest -x -q test_repro.py")


def test_proven_flip():
    engine = EvidenceEngine(OneShotGenerator(), LocalExecutor(), _cfg())
    claim = Claim(
        text="last_n drops the last row",
        repo_path=str(FIXTURES / "buggy_slice"),
        expected_signature="AssertionError",
    )
    case = engine.investigate(
        claim,
        mode=Mode.DETECTIVE,
        target=RepoState(path=str(FIXTURES / "buggy_slice"), label="target"),
        base=RepoState(path=str(FIXTURES / "fixed_slice"), label="base"),
    )
    assert case.verdict is Verdict.PROVEN, case.silence_reason
    assert case.test_file is not None
    assert case.evidence.deterministic
    assert case.evidence.fail_signature and "AssertionError" in case.evidence.fail_signature
    assert case.evidence.pass_log  # base ran and passed


def test_realistic_inventory_fixture_proves_missing_sku_key_error():
    engine = EvidenceEngine(InventoryGenerator(), LocalExecutor(), _cfg())
    claim = Claim(
        text="stock_for should return zero for an unknown SKU instead of raising KeyError",
        repo_path=str(FIXTURES / "buggy_inventory"),
        expected_signature="KeyError",
    )

    case = engine.investigate(
        claim,
        target=RepoState(path=str(FIXTURES / "buggy_inventory"), label="target"),
        base=RepoState(path=str(FIXTURES / "fixed_inventory"), label="base"),
    )

    assert case.verdict is Verdict.PROVEN, case.silence_reason
    assert case.test_file and case.test_file.path == "test_inventory_repro.py"
    assert case.evidence.fail_signature and "KeyError" in case.evidence.fail_signature


def test_prosecutor_requires_failure_traceback_to_touch_inventory_diff():
    engine = EvidenceEngine(InventoryGenerator(), LocalExecutor(), _cfg())
    claim = Claim(
        text="PR makes unknown SKUs raise KeyError",
        repo_path=str(FIXTURES / "buggy_inventory"),
        expected_signature="KeyError",
    )

    case = engine.investigate(
        claim,
        mode=Mode.PROSECUTOR,
        target=RepoState(path=str(FIXTURES / "buggy_inventory"), label="target"),
        base=RepoState(path=str(FIXTURES / "fixed_inventory"), label="base"),
    )

    assert case.verdict is Verdict.PROVEN, case.silence_reason


def test_prosecutor_intent_judgment_is_separate_from_proven_verdict():
    judge = StaticIntentJudge(IntentJudgment.INTENDED)
    engine = EvidenceEngine(InventoryGenerator(), LocalExecutor(), _cfg(), intent_judge=judge)
    claim = Claim(
        text="PR makes unknown SKUs raise KeyError",
        repo_path=str(FIXTURES / "buggy_inventory"),
        expected_signature="KeyError",
    )

    case = engine.investigate(
        claim,
        mode=Mode.PROSECUTOR,
        target=RepoState(path=str(FIXTURES / "buggy_inventory"), label="target"),
        base=RepoState(path=str(FIXTURES / "fixed_inventory"), label="base"),
        intent_context="This PR intentionally makes missing inventory an error.",
    )

    assert case.verdict is Verdict.PROVEN
    assert case.intent_judgment is IntentJudgment.INTENDED
    assert case.disposition is Disposition.BEHAVIOR_CHANGE
    assert case.declared_behavior_delta == (
        "Preserve inventory lookup behavior during the refactor."
    )
    assert case.declared_delta_sources == ["pr_description"]
    assert case.intent_model == "test-intent-model"
    assert judge.calls == 1


def test_unintended_intent_relabels_but_does_not_change_proven_verdict():
    judge = StaticIntentJudge(IntentJudgment.UNINTENDED)
    engine = EvidenceEngine(InventoryGenerator(), LocalExecutor(), _cfg(), intent_judge=judge)
    claim = Claim(
        text="PR makes unknown SKUs raise KeyError",
        repo_path=str(FIXTURES / "buggy_inventory"),
        expected_signature="KeyError",
    )

    case = engine.investigate(
        claim,
        mode=Mode.PROSECUTOR,
        target=RepoState(path=str(FIXTURES / "buggy_inventory"), label="target"),
        base=RepoState(path=str(FIXTURES / "fixed_inventory"), label="base"),
        intent_context="Refactor only; preserve behavior.",
    )

    assert case.verdict is Verdict.PROVEN
    assert case.intent_judgment is IntentJudgment.UNINTENDED
    assert case.disposition is Disposition.PROVEN_REGRESSION


def test_engine_stops_at_first_admissible_candidate():
    engine = EvidenceEngine(TwoShotGenerator(), LocalExecutor(), _cfg())
    claim = Claim(text="last_n drops the last row", repo_path=str(FIXTURES / "buggy_slice"))

    case = engine.investigate(
        claim,
        target=RepoState(path=str(FIXTURES / "buggy_slice"), label="target"),
        base=RepoState(path=str(FIXTURES / "fixed_slice"), label="base"),
    )

    assert case.verdict is Verdict.PROVEN
    assert [hyp.text for hyp in case.hypotheses] == [
        "last_n drops the final element (off-by-one slice)"
    ]
    assert case.test_file and case.test_file.path == "test_repro.py"
    assert case.run_command.endswith("test_repro.py")


def test_control_state_is_recorded_and_must_pass():
    engine = EvidenceEngine(OneShotGenerator(), LocalExecutor(), _cfg())
    claim = Claim(text="last_n drops the last row", repo_path=str(FIXTURES / "buggy_slice"))

    case = engine.investigate(
        claim,
        target=RepoState(path=str(FIXTURES / "buggy_slice"), label="target"),
        base=RepoState(path=str(FIXTURES / "fixed_slice"), label="base"),
        control=RepoState(path=str(FIXTURES / "fixed_slice"), label="control"),
    )

    assert case.verdict is Verdict.PROVEN
    assert case.evidence.control_log
    assert [run.state for run in case.evidence.runs][-1] == "control"


def test_remote_commit_proven_case_records_benchmark_provenance():
    engine = EvidenceEngine(TwoShotGenerator(), LocalExecutor(), _cfg())
    claim = Claim(text="last_n drops the last row", repo_path=str(FIXTURES / "buggy_slice"))

    case = engine.investigate(
        claim,
        target=RepoState(path=str(FIXTURES / "buggy_slice"), label="target", commit="abc1234"),
        base=RepoState(path=str(FIXTURES / "fixed_slice"), label="base", commit="def5678"),
        repo_source="https://github.com/example/slicer.git",
    )

    assert case.repo == "https://github.com/example/slicer.git"
    assert case.base_commit == "abc1234"
    assert case.target_commit == "def5678"
    assert case.target_state.value == "pr_head"
    assert case.fail_to_pass == ["test_repro.py"]
    assert case.pass_to_pass == []


def test_base_only_yields_reproduced_when_allowed():
    # No fixed state, but allow_reproduced=True + a signature -> the weaker REPRODUCED
    # tier instead of silence or an overclaimed PROVEN.
    cfg = EngineConfig(
        reruns=3,
        run_command=f"{sys.executable} -m pytest -x -q test_repro.py",
        allow_reproduced=True,
    )
    engine = EvidenceEngine(OneShotGenerator(), LocalExecutor(), cfg)
    claim = Claim(
        text="last_n drops the last row",
        repo_path=str(FIXTURES / "buggy_slice"),
        expected_signature="AssertionError",
    )
    case = engine.investigate(
        claim,
        mode=Mode.DETECTIVE,
        target=RepoState(path=str(FIXTURES / "buggy_slice"), label="target"),
        base=None,
    )
    assert case.verdict is Verdict.REPRODUCED
    assert not case.is_proven()  # explicitly NOT a full flip
    assert case.is_evidence()
    assert case.fail_to_pass == []  # no proven pass side -> no benchmark pair


def test_engine_streams_each_execution_before_the_terminal_verdict():
    events: list[dict] = []
    engine = EvidenceEngine(
        OneShotGenerator(),
        LocalExecutor(),
        _cfg(),
        event_sink=events.append,
    )
    claim = Claim(text="last_n drops the last row", repo_path=str(FIXTURES / "buggy_slice"))

    engine.investigate(
        claim,
        target=RepoState(path=str(FIXTURES / "buggy_slice"), label="target"),
        base=RepoState(path=str(FIXTURES / "fixed_slice"), label="base"),
    )

    assert [event["event"] for event in events] == [
        "phase",
        "phase",
        "phase",
        "hypothesis",
        "run",
        "run",
        "run",
        "run",
        "verdict",
    ]
    assert [event["state"] for event in events if event["event"] == "run"] == [
        "target",
        "target",
        "target",
        "base",
    ]
    assert events[-1]["verdict"] == "PROVEN"


def test_silence_when_no_base_to_prove_pass_side():
    # BASE_ONLY: we can confirm a deterministic failure but cannot prove the flip.
    engine = EvidenceEngine(OneShotGenerator(), LocalExecutor(), _cfg())
    claim = Claim(
        text="last_n drops the last row",
        repo_path=str(FIXTURES / "buggy_slice"),
        expected_signature="AssertionError",
    )
    case = engine.investigate(
        claim,
        mode=Mode.DETECTIVE,
        target=RepoState(path=str(FIXTURES / "buggy_slice"), label="target"),
        base=None,
    )
    assert case.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert "pass side" in (case.silence_reason or "")


def test_stub_generator_stays_silent():
    # The stub emits a test that fails everywhere -> no admissible flip -> silence.
    engine = EvidenceEngine(StubGenerator(), LocalExecutor(), EngineConfig(reruns=2))
    claim = Claim(text="anything", repo_path=str(FIXTURES / "buggy_slice"))
    case = engine.investigate(claim, mode=Mode.DETECTIVE)
    assert case.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert case.silence_reason


class ExplodingExecutor(Executor):
    def prepare(self, repo: RepoState) -> str | None:
        return None

    def run(self, repo: RepoState, spec: ExecSpec):
        raise AssertionError("unsafe candidate reached the executor")


class SuiteCatchesExecutor(ExplodingExecutor):
    def run_suite(self, repo, argv, *, image=None, timeout_s=120):
        from exhibit_a.executor.base import ExecOutcome

        return ExecOutcome(1, "FAILED tests/test_existing.py", "")


class MustNotGenerate:
    def propose(self, claim, max_hypotheses=3):
        raise AssertionError("generator ran after the existing suite caught the failure")

    def refine(self, claim, feedback):
        raise AssertionError("refinement ran after the existing suite caught the failure")


def test_existing_suite_failure_stays_silent_before_generation():
    engine = EvidenceEngine(MustNotGenerate(), SuiteCatchesExecutor())
    claim = Claim(text="already covered", repo_path=str(FIXTURES / "buggy_slice"))

    case = engine.investigate(claim)

    assert case.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert case.existing_suite_passed is False
    assert case.suite_gap is False
    assert case.existing_suite_log == "FAILED tests/test_existing.py"
    assert case.hypotheses == []


class UnsafeCommandGenerator:
    def propose(self, claim: Claim, max_hypotheses: int = 3) -> list[Candidate]:
        return [
            Candidate(
                hypothesis="attempts to broaden execution scope",
                test_path="test_repro.py",
                test_code="def test_repro():\n    assert False\n",
                run_command="pytest -q test_repro.py; touch source.py",
            )
        ]

    def refine(self, claim: Claim, feedback: Feedback):
        return None


def test_engine_rejects_out_of_scope_command_before_execution():
    engine = EvidenceEngine(UnsafeCommandGenerator(), ExplodingExecutor())
    claim = Claim(text="anything", repo_path=str(FIXTURES / "buggy_slice"))

    case = engine.investigate(claim)

    assert case.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert "shell control" in (case.silence_reason or "")


class BrokenEnvironmentExecutor(ExplodingExecutor):
    def prepare(self, repo: RepoState) -> str | None:
        raise EnvironmentSetupError("no pinned lockfile")


def test_environment_build_failure_becomes_honest_silence():
    engine = EvidenceEngine(OneShotGenerator(), BrokenEnvironmentExecutor())
    claim = Claim(text="anything", repo_path=str(FIXTURES / "buggy_slice"))

    case = engine.investigate(claim)

    assert case.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert case.hypotheses == []
    assert case.silence_reason == "could not build environment: no pinned lockfile"


class FlakyExecutor(Executor):
    def __init__(self):
        self.calls = 0

    def prepare(self, repo: RepoState) -> str:
        return "exhibit-a-env:flaky-fixture"

    def run(self, repo: RepoState, spec: ExecSpec):
        from exhibit_a.executor.base import ExecOutcome

        self.calls += 1
        if self.calls == 2:
            return ExecOutcome(0, "1 passed", "")
        return ExecOutcome(1, "", "E   AssertionError: wrong value")


def test_flaky_rejection_retains_candidate_and_environment_for_private_quarantine():
    config = _cfg()
    config.check_existing_suite = False
    engine = EvidenceEngine(OneShotGenerator(), FlakyExecutor(), config)
    claim = Claim(
        text="last_n is intermittent",
        repo_path=str(FIXTURES / "buggy_slice"),
        expected_signature="AssertionError",
    )

    case = engine.investigate(claim)

    assert case.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert case.silence_reason and case.silence_reason.startswith("flaky on target")
    assert case.test_file and case.test_file.path == "test_repro.py"
    assert case.environment_ref == "exhibit-a-env:flaky-fixture"
