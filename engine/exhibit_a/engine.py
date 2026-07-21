"""The Evidence Engine — the shared core that ties every layer together.

Flow (plan §1 "Shared core"):
  claim + codebase(s) -> generator proposes candidate tests
    -> executor runs each on the target (buggy) state, N times for determinism
    -> if a base/fixed state exists, run there too
    -> flip check decides admissibility
    -> emit a Case: PROVEN (with evidence) or INSUFFICIENT_EVIDENCE (Silence Log)

The engine never lets a model's claim override an execution result. It owns
Execute + Judge; the generator owns the model reasoning. Bounded refinement retries
per the plan; on exhaustion the verdict is honest silence, not a guess.
"""

from __future__ import annotations

import shlex
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Callable, Optional

from .executor.base import EnvironmentSetupError, ExecSpec, Executor, RepoState
from .hypothesis.generator import Candidate, Claim, Feedback, HypothesisGenerator
from .hypothesis.intent import IntentJudge
from .models.case import (
    Case,
    derive_disposition,
    Evidence,
    EvidenceMinimization,
    Hypothesis,
    IntentJudgment,
    Mode,
    RunResult,
    TargetKind,
    TestArtifact,
    Verdict,
)
from .verdict.flip_check import extract_signature, flip_check
from .verdict.diff_location import ChangedLines, changed_line_map
from .verdict.minimization import minimize_proven_test


@dataclass
class EngineConfig:
    reruns: int = 5  # determinism gate: rerun the target this many times
    max_refine: int = 3  # bounded refinement retries per the plan
    timeout_s: int = 120
    run_command: str = "pytest -x -q"
    # Detective on a live bug often has no fixed state to flip against. When True,
    # a deterministic, signature-matched failure without a pass side yields the
    # weaker Verdict.REPRODUCED instead of silence. Prosecutor keeps this False so
    # only a full flip speaks.
    allow_reproduced: bool = False
    check_existing_suite: bool = True
    suite_command: tuple[str, ...] = ("python3", "-m", "pytest", "-q")
    minimize_proven: bool = True
    minimization_max_attempts: int = 24


class EvidenceEngine:
    def __init__(
        self,
        generator: HypothesisGenerator,
        executor: Executor,
        config: Optional[EngineConfig] = None,
        event_sink: Optional[Callable[[dict[str, Any]], None]] = None,
        intent_judge: IntentJudge | None = None,
    ):
        self.generator = generator
        self.executor = executor
        self.config = config or EngineConfig()
        self.event_sink = event_sink
        self.intent_judge = intent_judge

    def investigate(
        self,
        claim: Claim,
        *,
        mode: Mode = Mode.DETECTIVE,
        base: Optional[RepoState] = None,
        target: Optional[RepoState] = None,
        control: Optional[RepoState] = None,
        repo_source: Optional[str] = None,
        intent_context: str | None = None,
    ) -> Case:
        """Run the full evidence loop and return a Case.

        `target` is the buggy checkout (required). `base` is the fixed/base state;
        None means BASE_ONLY (Detective) where the pass side can't yet be proven.
        """
        if target is None:
            target = RepoState(path=claim.repo_path, label="target")

        case = Case(
            id=uuid.uuid4().hex[:12],
            mode=mode,
            repo=repo_source or claim.repo_path,
            base_commit=target.commit,
            target_commit=base.commit if base else None,
            target_state=(
                TargetKind.BASE_ONLY
                if base is None
                else TargetKind.PR_HEAD
                if target.commit and base.commit
                else TargetKind.SYNTHESIZED_PATCH
            ),
            claim_text=claim.text,
            run_command=self.config.run_command,
        )

        try:
            target_image = self.executor.prepare(target)
            base_image = self.executor.prepare(base) if base is not None else None
            control_image = self.executor.prepare(control) if control is not None else None
        except EnvironmentSetupError as exc:
            case.silence_reason = f"could not build environment: {exc}"
            self._emit(
                "verdict",
                verdict="INSUFFICIENT_EVIDENCE",
                reason=case.silence_reason,
            )
            return case
        case.environment_ref = target_image or "local-unisolated"

        if self.config.check_existing_suite:
            self._emit(
                "phase",
                phase="existing_suite",
                message="Checking whether the repository suite already catches this",
            )
            suite = self.executor.run_suite(
                target,
                list(self.config.suite_command),
                image=target_image,
                timeout_s=self.config.timeout_s,
            )
            if suite is not None:
                case.existing_suite_log = suite.log
                if suite.exit_code in {0, 5} and not suite.timed_out:
                    case.existing_suite_passed = True
                elif suite.exit_code == 1 and not suite.timed_out:
                    case.existing_suite_passed = False
                    case.suite_gap = False
                    case.silence_reason = "existing test suite already fails; CI has this signal"
                    self._emit(
                        "verdict",
                        verdict="INSUFFICIENT_EVIDENCE",
                        reason=case.silence_reason,
                    )
                    return case
                else:
                    case.silence_reason = (
                        f"existing test suite could not be evaluated safely: exit {suite.exit_code}"
                    )
                    self._emit(
                        "verdict",
                        verdict="INSUFFICIENT_EVIDENCE",
                        reason=case.silence_reason,
                    )
                    return case

        changed_lines: ChangedLines | None = None
        if mode is Mode.PROSECUTOR:
            if base is None:
                case.silence_reason = "Prosecutor mode requires a base checkout for diff matching"
                self._emit(
                    "verdict",
                    verdict="INSUFFICIENT_EVIDENCE",
                    reason=case.silence_reason,
                )
                return case
            changed_lines = changed_line_map(base.path, target.path)

        self._emit("phase", phase="generating", message="Localizing the claim with Codex")
        candidates = self.generator.propose(claim)
        self._emit("phase", phase="executing", message=f"Testing {len(candidates)} hypotheses")
        if not candidates:
            generator_error = getattr(self.generator, "last_error", None)
            if generator_error:
                case.silence_reason = generator_error
        attempts = 0

        for cand in candidates:
            result = self._try_candidate(
                claim,
                cand,
                base,
                target,
                case,
                base_image,
                target_image,
                changed_lines,
                control,
                control_image,
            )
            if case.is_evidence():
                if case.existing_suite_passed is True:
                    case.suite_gap = True
                self._assess_intent(case, target, intent_context)
                case.disposition = derive_disposition(case.verdict, case.intent_judgment)
                return case

            # Bounded refinement on the most recent failed candidate.
            feedback = result
            while feedback is not None and attempts < self.config.max_refine:
                attempts += 1
                refined = self.generator.refine(claim, feedback)
                if refined is None:
                    break
                feedback = self._try_candidate(
                    claim,
                    refined,
                    base,
                    target,
                    case,
                    base_image,
                    target_image,
                    changed_lines,
                    control,
                    control_image,
                )
                if case.is_evidence():
                    if case.existing_suite_passed is True:
                        case.suite_gap = True
                    self._assess_intent(case, target, intent_context)
                    case.disposition = derive_disposition(case.verdict, case.intent_judgment)
                    return case

        # Nothing cleared the gate -> honest silence.
        if not case.is_evidence():
            case.verdict = Verdict.INSUFFICIENT_EVIDENCE
            if not case.silence_reason:
                case.silence_reason = (
                    "no candidate produced a deterministic, signature-matching flip"
                )
            self._emit(
                "verdict",
                verdict="INSUFFICIENT_EVIDENCE",
                reason=case.silence_reason,
            )
        return case

    def _assess_intent(self, case: Case, target: RepoState, intent_context: str | None) -> None:
        if (
            case.mode is not Mode.PROSECUTOR
            or not case.is_proven()
            or self.intent_judge is None
            or not intent_context
        ):
            return
        assessment = self.intent_judge.assess(target.path, case.claim_text, intent_context)
        if assessment is None:
            case.intent_judgment = IntentJudgment.UNCLEAR
            case.intent_rationale = getattr(
                self.intent_judge, "last_error", "Intent assessment returned no result"
            )
            return
        case.intent_judgment = assessment.judgment
        case.intent_rationale = assessment.rationale
        case.intent_model = assessment.model
        case.declared_behavior_delta = assessment.declared_behavior_delta
        case.declared_delta_sources = list(assessment.declared_delta_sources)
        self._emit(
            "intent",
            judgment=assessment.judgment.value,
            rationale=assessment.rationale,
            model=assessment.model,
            declared_behavior_delta=assessment.declared_behavior_delta,
            declared_delta_sources=list(assessment.declared_delta_sources),
        )

    def _try_candidate(
        self,
        claim: Claim,
        cand: Candidate,
        base: Optional[RepoState],
        target: RepoState,
        case: Case,
        base_image: str | None,
        target_image: str | None,
        changed_lines: ChangedLines | None,
        control: RepoState | None,
        control_image: str | None,
    ) -> Optional[Feedback]:
        """Execute one candidate through the gates. Mutates `case`. Returns Feedback
        if the candidate was NOT admissible (to drive refinement), or None if proven.
        """
        hyp = Hypothesis(text=cand.hypothesis)
        case.hypotheses.append(hyp)
        self._emit("hypothesis", text=cand.hypothesis, test_path=cand.test_path)

        policy_reason = candidate_policy_reason(cand)
        if policy_reason:
            hyp.rejected = True
            hyp.reason = policy_reason
            case.silence_reason = policy_reason
            return Feedback(
                candidate=cand,
                fail_log="",
                passed_on_target=False,
                admissible=False,
                reason=policy_reason,
                rejected_hypotheses=[cand.hypothesis],
            )

        spec = ExecSpec(
            test_path=cand.test_path,
            test_code=cand.test_code,
            command=cand.run_command or self.config.run_command,
            timeout_s=self.config.timeout_s,
        )
        target_spec = ExecSpec(**{**spec.__dict__, "image": target_image})
        base_spec = ExecSpec(**{**spec.__dict__, "image": base_image})
        control_spec = ExecSpec(**{**spec.__dict__, "image": control_image})

        # Run on the target (buggy) state N times for the determinism gate.
        target_outcomes = []
        run_records: list[RunResult] = []
        for attempt in range(1, self.config.reruns + 1):
            out = self.executor.run(target, target_spec)
            target_outcomes.append(out)
            run_records.append(
                RunResult(
                    state="target",
                    exit_code=out.exit_code,
                    passed=out.passed,
                    log=out.log,
                    signature=extract_signature(out),
                    duration_s=out.duration_s,
                )
            )
            self._emit(
                "run",
                state="target",
                attempt=attempt,
                total=self.config.reruns,
                passed=out.passed,
                exit_code=out.exit_code,
                log=out.log,
                signature=extract_signature(out),
                duration_s=out.duration_s,
            )

        base_outcome = None
        if base is not None:
            base_outcome = self.executor.run(base, base_spec)
            run_records.append(
                RunResult(
                    state="base",
                    exit_code=base_outcome.exit_code,
                    passed=base_outcome.passed,
                    log=base_outcome.log,
                    duration_s=base_outcome.duration_s,
                )
            )
            self._emit(
                "run",
                state="base",
                attempt=1,
                total=1,
                passed=base_outcome.passed,
                exit_code=base_outcome.exit_code,
                log=base_outcome.log,
                signature=extract_signature(base_outcome),
                duration_s=base_outcome.duration_s,
            )

        control_outcome = None
        if control is not None:
            control_outcome = self.executor.run(control, control_spec)
            run_records.append(
                RunResult(
                    state="control",
                    exit_code=control_outcome.exit_code,
                    passed=control_outcome.passed,
                    log=control_outcome.log,
                    signature=extract_signature(control_outcome),
                    duration_s=control_outcome.duration_s,
                )
            )
            self._emit(
                "run",
                state="control",
                attempt=1,
                total=1,
                passed=control_outcome.passed,
                exit_code=control_outcome.exit_code,
                log=control_outcome.log,
                signature=extract_signature(control_outcome),
                duration_s=control_outcome.duration_s,
            )

        expected = cand.expected_signature or claim.expected_signature
        flip = flip_check(
            target_runs=target_outcomes,
            base_run=base_outcome,
            test_code=cand.test_code,
            expected_signature=expected,
            allow_reproduced=self.config.allow_reproduced,
            changed_lines=changed_lines,
            control_run=control_outcome,
        )

        if flip.admissible:
            verdict = Verdict.PROVEN if flip.tier == "flip" else Verdict.REPRODUCED
            case.verdict = verdict
            case.run_command = spec.command
            case.test_file = TestArtifact(path=cand.test_path, code=cand.test_code)
            case.root_cause_narrative = cand.hypothesis
            # Only a full flip yields a benchmark FAIL_TO_PASS pair; a bare
            # reproduction has no proven pass side to record.
            if verdict is Verdict.PROVEN:
                case.fail_to_pass = [cand.test_path]
            case.evidence = Evidence(
                fail_log=target_outcomes[0].log,
                fail_signature=flip.fail_signature,
                pass_log=base_outcome.log if base_outcome else "",
                control_log=control_outcome.log if control_outcome else "",
                reruns=self.config.reruns,
                deterministic=flip.deterministic,
                runs=run_records,
            )
            if verdict is Verdict.PROVEN and base is not None and self.config.minimize_proven:
                self._minimize_evidence(
                    case=case,
                    spec=spec,
                    expected_signature=expected,
                    base=base,
                    target=target,
                    control=control,
                    base_image=base_image,
                    target_image=target_image,
                    control_image=control_image,
                    changed_lines=changed_lines,
                )
            self._emit("verdict", verdict=verdict.value, hypothesis=cand.hypothesis)
            return None

        if flip.reason and flip.reason.startswith("flaky on target"):
            case.test_file = TestArtifact(path=cand.test_path, code=cand.test_code)
            case.run_command = spec.command

        # Not admissible: record why, mark hypothesis rejected, return feedback.
        hyp.rejected = True
        hyp.reason = flip.reason
        case.silence_reason = flip.reason
        case.evidence = Evidence(
            fail_log=target_outcomes[0].log if target_outcomes else "",
            fail_signature=flip.fail_signature,
            pass_log=base_outcome.log if base_outcome else "",
            control_log=control_outcome.log if control_outcome else "",
            reruns=self.config.reruns,
            deterministic=flip.deterministic,
            runs=run_records,
        )
        self._emit(
            "rejected",
            hypothesis=cand.hypothesis,
            reason=flip.reason,
        )
        return Feedback(
            candidate=cand,
            fail_log=target_outcomes[0].log if target_outcomes else "",
            passed_on_target=all(o.passed for o in target_outcomes),
            admissible=False,
            reason=flip.reason,
            rejected_hypotheses=[cand.hypothesis],
        )

    def _minimize_evidence(
        self,
        *,
        case: Case,
        spec: ExecSpec,
        expected_signature: str | None,
        base: RepoState,
        target: RepoState,
        control: RepoState | None,
        base_image: str | None,
        target_image: str | None,
        control_image: str | None,
        changed_lines: ChangedLines | None,
    ) -> None:
        """Run an optional post-verdict shrink pass; never modify the verdict."""
        original = TestArtifact(path=spec.test_path, code=spec.test_code)
        case.original_test_file = original
        self._emit(
            "phase",
            phase="minimizing",
            message="Shrinking the proof under the deterministic flip check",
        )
        try:
            result = minimize_proven_test(
                executor=self.executor,
                target=target,
                base=base,
                control=control,
                spec=spec,
                expected_signature=expected_signature,
                reruns=self.config.reruns,
                changed_lines=changed_lines,
                target_image=target_image,
                base_image=base_image,
                control_image=control_image,
                max_attempts=self.config.minimization_max_attempts,
            )
        except Exception as exc:
            case.minimization = EvidenceMinimization(
                original_lines=len(spec.test_code.splitlines()),
                minimized_lines=len(spec.test_code.splitlines()),
                reason=f"minimization could not be completed: {exc}",
            )
            self._emit("minimization", verified=False, reason=str(exc))
            return

        case.minimization = EvidenceMinimization(
            verified=result.verified,
            attempts=result.attempts,
            accepted=result.accepted,
            original_lines=result.original_lines,
            minimized_lines=result.minimized_lines,
            reduction_ratio=result.reduction_ratio,
            reason=None if result.verified else "minimized artifact was not independently verified",
        )
        if result.verified:
            case.test_file = result.artifact
        self._emit(
            "minimization",
            verified=result.verified,
            attempts=result.attempts,
            accepted=result.accepted,
            original_lines=result.original_lines,
            minimized_lines=result.minimized_lines,
            reduction_ratio=result.reduction_ratio,
        )

    def _emit(self, event: str, **payload: Any) -> None:
        if self.event_sink is not None:
            self.event_sink({"event": event, **payload})


def candidate_policy_reason(cand: Candidate) -> str | None:
    """Reject candidates that could escape the single generated pytest file."""
    path = PurePosixPath(cand.test_path)
    if (
        not cand.test_path
        or path.is_absolute()
        or ".." in path.parts
        or path.suffix != ".py"
        or not path.name.startswith("test_")
    ):
        return "candidate test path is outside the allowed pytest scope"

    if any(marker in cand.run_command for marker in (";", "&", "|", ">", "<", "`", "$")):
        return "candidate run command contains a shell control character"
    try:
        argv = shlex.split(cand.run_command)
    except ValueError:
        return "candidate run command is not parseable"

    if len(argv) >= 3 and argv[1:3] == ["-m", "pytest"]:
        pytest_args = argv[3:]
    elif argv and PurePosixPath(argv[0]).name in {"pytest", "pytest3"}:
        pytest_args = argv[1:]
    else:
        return "candidate run command must invoke pytest directly"

    allowed_flags = {"-x", "-q", "--tb=short", "--disable-warnings"}
    positional = [arg for arg in pytest_args if not arg.startswith("-")]
    flags = [arg for arg in pytest_args if arg.startswith("-")]
    if positional != [cand.test_path] or any(flag not in allowed_flags for flag in flags):
        return "candidate run command must target only the generated test file"
    return None
