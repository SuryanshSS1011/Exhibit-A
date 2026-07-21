import json
from pathlib import Path

from exhibit_a.models.case import (
    Case,
    Evidence,
    Hypothesis,
    Mode,
    RunResult,
    TestArtifact as CaseTestArtifact,
    Verdict,
)
from exhibit_a.store.research import RESEARCH_SCHEMA, ResearchStore


def test_flaky_quarantine_preserves_candidate_environment_and_raw_runs(tmp_path: Path):
    case = Case(id="flaky-1", mode=Mode.DETECTIVE, claim_text="intermittent lookup")
    case.environment_ref = "exhibit-a-env:abc"
    case.hypotheses = [Hypothesis("lookup race", True, "flaky on target: failed 2/5 reruns")]
    case.test_file = CaseTestArtifact("test_repro.py", "from app import lookup\nassert lookup()\n")
    case.evidence = Evidence(
        reruns=2,
        runs=[RunResult("target", 1, False, "E   AssertionError", "AssertionError")],
    )

    path = ResearchStore(tmp_path).record_flaky(case, model="gpt-5.6-sol")

    assert path is not None
    payload = json.loads(path.read_text())
    assert payload["schema_version"] == RESEARCH_SCHEMA
    assert payload["model_version"] == "gpt-5.6-sol"
    assert payload["environment_ref"] == "exhibit-a-env:abc"
    assert payload["test_file"]["path"] == "test_repro.py"
    assert payload["runs"][0]["passed"] is False


def test_observatory_registration_and_longitudinal_history_are_versioned(tmp_path: Path):
    case = Case(id="proven-1", mode=Mode.DETECTIVE, verdict=Verdict.PROVEN)
    case.test_file = CaseTestArtifact("test_repro.py", "from app import value\nassert value()\n")
    case.evidence.fail_signature = "AssertionError"
    store = ResearchStore(tmp_path)

    path = store.register_observatory(case, model="gpt-5.6-sol", interval_days=7)
    assert path is not None
    store.record_observation(
        case.id,
        upstream_sha="a" * 40,
        status="healthy",
        runs=[{"exit_code": 0, "passed": True, "log": "1 passed"}],
    )

    payload = json.loads(path.read_text())
    assert payload["schema_version"] == RESEARCH_SCHEMA
    assert payload["interval_days"] == 7
    assert payload["history"][0]["status"] == "healthy"
    assert payload["history"][0]["upstream_sha"] == "a" * 40
