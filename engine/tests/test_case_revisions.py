"""What `base_commit` and `target_commit` mean, pinned so a reader cannot guess wrong.

Both fields are published in passports and EEF archives, and every consumer treats them as
opaque hex, so nothing downstream would break if they were swapped -- a reader of the
artifact would simply be told the wrong thing. That makes this a contract worth asserting
rather than inferring.

The confusion is real: `RepoState.label` uses "target" for the state under test, which in a
Detective run is the *buggy* revision, and "base" for the comparison state, which is the
*fixed* one. So the RepoState labelled "target" supplies `base_commit`. It has already been
read as a reversal once. It is not one.
"""

from __future__ import annotations

import sys
from pathlib import Path

from exhibit_a import EngineConfig, EvidenceEngine
from exhibit_a.executor.base import RepoState
from exhibit_a.executor.local_exec import LocalExecutor
from exhibit_a.hypothesis.generator import Candidate, Claim, Feedback
from exhibit_a.models.case import Mode, Verdict

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

FLIP_TEST = (
    "from slicer import last_n\n\n"
    "def test_last_n_keeps_final_element():\n"
    "    assert last_n([1, 2, 3, 4], 2) == [3, 4]\n"
)


class _Generator:
    def propose(self, claim: Claim, max_hypotheses: int = 3) -> list[Candidate]:
        return [
            Candidate(
                hypothesis="last_n drops the final element",
                test_path="test_repro.py",
                test_code=FLIP_TEST,
                run_command=f"{sys.executable} -m pytest -x -q test_repro.py",
                expected_signature="AssertionError",
            )
        ]

    def refine(self, claim: Claim, feedback: Feedback) -> None:
        return None


def test_base_commit_is_the_revision_before_and_target_commit_the_one_after():
    engine = EvidenceEngine(
        _Generator(),
        LocalExecutor(),
        EngineConfig(
            reruns=1,
            check_existing_suite=False,
            run_command=f"{sys.executable} -m pytest -x -q test_repro.py",
            minimize_proven=False,
            score_evidence_strength=False,
        ),
    )
    # The buggy revision is the state under test, so it is the RepoState labelled "target".
    buggy = RepoState(path=str(FIXTURES / "buggy_slice"), label="target", commit="a" * 40)
    fixed = RepoState(path=str(FIXTURES / "fixed_slice"), label="base", commit="b" * 40)

    case = engine.investigate(
        Claim(text="last_n drops the last row", repo_path=buggy.path),
        mode=Mode.DETECTIVE,
        target=buggy,
        base=fixed,
    )

    assert case.verdict is Verdict.VERIFIED, case.silence_reason
    # Named from the change's point of view, not the executor's.
    assert case.base_commit == "a" * 40, "base_commit is the revision the change started from"
    assert case.target_commit == "b" * 40, "target_commit is the revision it became"
