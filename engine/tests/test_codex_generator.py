from __future__ import annotations

from pathlib import Path

from exhibit_a.hypothesis.generator import Claim, CodexGenerator, Feedback


class FakeCodexGenerator(CodexGenerator):
    def __init__(self, responses: list[dict]):
        super().__init__(model="gpt-5.6-sol")
        self.responses = responses
        self.prompts: list[str] = []

    def _invoke(self, repo: Path, prompt: str, schema: dict) -> dict:
        self.prompts.append(prompt)
        return self.responses.pop(0)


def _raw_candidate(test_path: str = "tests/test_repro.py") -> dict:
    return {
        "hypothesis": "last_n excludes the final list element",
        "test_path": test_path,
        "test_code": (
            "from slicer import last_n\n\n"
            "def test_last_n_keeps_final_element():\n"
            "    assert last_n([1, 2, 3], 2) == [2, 3]\n"
        ),
        "expected_signature": "AssertionError",
        "notes": "Localized to slicer.py and inverted the observed truncated result.",
    }


def test_propose_converts_structured_output_to_scoped_pytest_candidate(tmp_path: Path):
    generator = FakeCodexGenerator([{"candidates": [_raw_candidate()]}])

    candidates = generator.propose(Claim("last_n drops the last row", str(tmp_path)))

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.run_command == "python3 -m pytest -x -q tests/test_repro.py"
    assert candidate.expected_signature == "AssertionError"
    assert "pass-then-invert" in generator.prompts[0]


def test_propose_rejects_path_traversal(tmp_path: Path):
    generator = FakeCodexGenerator([{"candidates": [_raw_candidate("../test_escape.py")]}])

    assert generator.propose(Claim("claim", str(tmp_path))) == []
    assert generator.last_error == "Codex generation failed: unsafe test path: '../test_escape.py'"


def test_refine_can_decline_an_unjustified_retry(tmp_path: Path):
    generator = FakeCodexGenerator([{"candidate": None}])
    candidate = CodexGenerator()._candidate(_raw_candidate())
    feedback = Feedback(
        candidate=candidate,
        fail_log="collected 0 items",
        passed_on_target=False,
        admissible=False,
        reason="test did not collect",
    )

    assert generator.refine(Claim("claim", str(tmp_path)), feedback) is None
    assert "test did not collect" in generator.prompts[0]
