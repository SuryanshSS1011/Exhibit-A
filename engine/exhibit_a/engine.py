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

from .executor.base import ExecSpec, Executor, RepoState
from .hypothesis.generator import Candidate, Claim, Feedback, HypothesisGenerator
from .models.case import (
    Case,
    Evidence,
    Hypothesis,
    Mode,
    RunResult,
    TargetKind,
    TestArtifact,
    Verdict,
)
from .verdict.flip_check import extract_signature, flip_check


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


class EvidenceEngine:
    def __init__(
        self,
        generator: HypothesisGenerator,
        executor: Executor,
        config: Optional[EngineConfig] = None,
        event_sink: Optional[Callable[[dict[str, Any]], None]] = None,
    ):
        self.generator = generator
        self.executor = executor
        self.config = config or EngineConfig()
        self.event_sink = event_sink

    def investigate(
        self,
        claim: Claim,
        *,
        mode: Mode = Mode.DETECTIVE,
        base: Optional[RepoState] = None,
        target: Optional[RepoState] = None,
        repo_source: Optional[str] = None,
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

        self._emit("phase", phase="generating", message="Localizing the claim with Codex")
        candidates = self.generator.propose(claim)
        self._emit("phase", phase="executing", message=f"Testing {len(candidates)} hypotheses")
        if not candidates:
            generator_error = getattr(self.generator, "last_error", None)
            if generator_error:
                case.silence_reason = generator_error
        attempts = 0

        for cand in candidates:
            result = self._try_candidate(claim, cand, base, target, case)
            if case.is_evidence():
                return case

            # Bounded refinement on the most recent failed candidate.
            feedback = result
            while feedback is not None and attempts < self.config.max_refine:
                attempts += 1
                refined = self.generator.refine(claim, feedback)
                if refined is None:
                    break
                feedback = self._try_candidate(claim, refined, base, target, case)
                if case.is_evidence():
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

    def _try_candidate(
        self,
        claim: Claim,
        cand: Candidate,
        base: Optional[RepoState],
        target: RepoState,
        case: Case,
    ) -> Optional[Feedback]:
        """Execute one candidate through the gates. Mutates `case`. Returns Feedback
        if the candidate was NOT admissible (to drive refinement), or None if proven.
        """
        hyp = Hypothesis(text=cand.hypothesis)
        case.hypotheses.append(hyp)
        self._emit("hypothesis", text=cand.hypothesis, test_path=cand.test_path)

        policy_reason = _candidate_policy_reason(cand)
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

        # Run on the target (buggy) state N times for the determinism gate.
        target_outcomes = []
        run_records: list[RunResult] = []
        for attempt in range(1, self.config.reruns + 1):
            out = self.executor.run(target, spec)
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
            base_outcome = self.executor.run(base, spec)
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

        expected = cand.expected_signature or claim.expected_signature
        flip = flip_check(
            target_runs=target_outcomes,
            base_run=base_outcome,
            test_code=cand.test_code,
            expected_signature=expected,
            allow_reproduced=self.config.allow_reproduced,
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
                reruns=self.config.reruns,
                deterministic=flip.deterministic,
                runs=run_records,
            )
            self._emit("verdict", verdict=verdict.value, hypothesis=cand.hypothesis)
            return None

        # Not admissible: record why, mark hypothesis rejected, return feedback.
        hyp.rejected = True
        hyp.reason = flip.reason
        case.silence_reason = flip.reason
        case.evidence = Evidence(
            fail_log=target_outcomes[0].log if target_outcomes else "",
            fail_signature=flip.fail_signature,
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

    def _emit(self, event: str, **payload: Any) -> None:
        if self.event_sink is not None:
            self.event_sink({"event": event, **payload})


def _candidate_policy_reason(cand: Candidate) -> str | None:
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
