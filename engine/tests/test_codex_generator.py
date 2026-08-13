from __future__ import annotations

from pathlib import Path

from exhibit_a.hypothesis.generator import Claim, CodexGenerator, Feedback
from exhibit_a.providers import ProviderRequest, ProviderResponse, RuntimeModel


class FakeProvider:
    def __init__(self, responses: list[dict]):
        self.responses = responses
        self.requests: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        return ProviderResponse(
            output=self.responses.pop(0),
            runtime_model=RuntimeModel("fake", "requested", "actual", "1"),
        )


def _generator(responses: list[dict]) -> tuple[CodexGenerator, FakeProvider]:
    provider = FakeProvider(responses)
    return CodexGenerator(provider=provider), provider


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
    generator, provider = _generator([{"candidates": [_raw_candidate()]}])

    candidates = generator.propose(Claim("last_n drops the last row", str(tmp_path)))

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.run_command == "python3 -m pytest -x -q tests/test_repro.py"
    assert candidate.expected_signature == "AssertionError"
    assert "pass-then-invert" in provider.requests[0].prompt
    assert generator.last_response is not None
    assert generator.last_response.runtime_model.confirmed_model == "actual"


def test_propose_rejects_path_traversal(tmp_path: Path):
    generator, _ = _generator([{"candidates": [_raw_candidate("../test_escape.py")]}])

    assert generator.propose(Claim("claim", str(tmp_path))) == []
    assert generator.last_error == "Codex generation failed: unsafe test path: '../test_escape.py'"


def test_refine_can_decline_an_unjustified_retry(tmp_path: Path):
    generator, provider = _generator([{"candidate": None}])
    candidate = CodexGenerator()._candidate(_raw_candidate())
    feedback = Feedback(
        candidate=candidate,
        fail_log="collected 0 items",
        passed_on_target=False,
        admissible=False,
        reason="test did not collect",
    )

    assert generator.refine(Claim("claim", str(tmp_path)), feedback) is None
    assert "test did not collect" in provider.requests[0].prompt
