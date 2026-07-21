from pathlib import Path

from exhibit_a.hypothesis.intent import CodexIntentJudge
from exhibit_a.models.case import IntentJudgment


class FakeStructuredCodex:
    model = "gpt-5.6-sol"

    def __init__(self, payload: dict):
        self.payload = payload
        self.prompt = ""

    def _invoke(self, repo: Path, prompt: str, schema: dict) -> dict:
        self.prompt = prompt
        return self.payload


def test_codex_intent_judge_labels_output_as_fallible_and_separate(tmp_path: Path):
    codex = FakeStructuredCodex(
        {
            "judgment": "unintended",
            "rationale": "The PR promises behavior preservation.",
        }
    )
    judge = CodexIntentJudge(codex)  # type: ignore[arg-type]

    assessment = judge.assess(
        str(tmp_path),
        "unknown SKUs now raise KeyError",
        "Refactor only; no behavior changes intended.",
    )

    assert assessment is not None
    assert assessment.judgment is IntentJudgment.UNINTENDED
    assert assessment.model == "gpt-5.6-sol"
    assert "override that evidence verdict" in codex.prompt.lower()
    assert "Choose unclear" in codex.prompt


def test_codex_intent_judge_fails_closed_on_invalid_output(tmp_path: Path):
    judge = CodexIntentJudge(FakeStructuredCodex({"judgment": "certain", "rationale": "x"}))  # type: ignore[arg-type]

    assert judge.assess(str(tmp_path), "delta", "context") is None
    assert judge.last_error and "Intent assessment failed" in judge.last_error
