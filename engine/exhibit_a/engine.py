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

import uuid
from dataclasses import dataclass
from typing import Optional

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


class EvidenceEngine:
    def __init__(
        self,
        generator: HypothesisGenerator,
        executor: Executor,
        config: Optional[EngineConfig] = None,
    ):
        self.generator = generator
        self.executor = executor
        self.config = config or EngineConfig()

    def investigate(
        self,
        claim: Claim,
        *,
        mode: Mode = Mode.DETECTIVE,
        base: Optional[RepoState] = None,
        target: Optional[RepoState] = None,
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
            repo=claim.repo_path,
            base_commit=base.commit if base else target.commit,
            target_state=(
                TargetKind.BASE_ONLY if base is None else TargetKind.SYNTHESIZED_PATCH
            ),
            claim_text=claim.text,
            run_command=self.config.run_command,
        )

        candidates = self.generator.propose(claim)
        attempts = 0

        for cand in candidates:
            result = self._try_candidate(claim, cand, base, target, case)
            if result is not None and case.is_proven():
                return case

            # Bounded refinement on the most recent failed candidate.
            feedback = result
            while feedback is not None and attempts < self.config.max_refine:
                attempts += 1
                refined = self.generator.refine(claim, feedback)
                if refined is None:
                    break
                feedback = self._try_candidate(claim, refined, base, target, case)
                if case.is_proven():
                    return case

        # Nothing cleared the gate -> honest silence.
        if not case.is_proven():
            case.verdict = Verdict.INSUFFICIENT_EVIDENCE
            if not case.silence_reason:
                case.silence_reason = (
                    "no candidate produced a deterministic, signature-matching flip"
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

        spec = ExecSpec(
            test_path=cand.test_path,
            test_code=cand.test_code,
            command=cand.run_command or self.config.run_command,
            timeout_s=self.config.timeout_s,
        )

        # Run on the target (buggy) state N times for the determinism gate.
        target_outcomes = []
        run_records: list[RunResult] = []
        for _ in range(self.config.reruns):
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

        expected = cand.expected_signature or claim.expected_signature
        flip = flip_check(
            target_runs=target_outcomes,
            base_run=base_outcome,
            test_code=cand.test_code,
            expected_signature=expected,
        )

        if flip.admissible:
            case.verdict = Verdict.PROVEN
            case.test_file = TestArtifact(path=cand.test_path, code=cand.test_code)
            case.root_cause_narrative = cand.hypothesis
            case.evidence = Evidence(
                fail_log=target_outcomes[0].log,
                fail_signature=flip.fail_signature,
                pass_log=base_outcome.log if base_outcome else "",
                reruns=self.config.reruns,
                deterministic=flip.deterministic,
                runs=run_records,
            )
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
        return Feedback(
            candidate=cand,
            fail_log=target_outcomes[0].log if target_outcomes else "",
            passed_on_target=all(o.passed for o in target_outcomes),
            admissible=False,
            reason=flip.reason,
            rejected_hypotheses=[cand.hypothesis],
        )
