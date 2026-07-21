import json
from pathlib import Path

from exhibit_a.models.case import Case, Mode
from exhibit_a.store.suite_gap import ENGINE_VERSION, SCHEMA_VERSION, SuiteGapStore


def test_suite_gap_register_is_private_local_and_versioned(tmp_path: Path):
    case = Case(id="case-1", mode=Mode.DETECTIVE, claim_text="missed regression")
    case.repo = "https://github.com/example/project.git"
    case.suite_gap = True
    case.existing_suite_passed = True
    case.existing_suite_log = "18 passed"

    path = SuiteGapStore(tmp_path / "private-register").save(case, model="gpt-5.6-sol")

    assert path is not None
    payload = json.loads(path.read_text())
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["engine_version"] == ENGINE_VERSION
    assert payload["model_version"] == "gpt-5.6-sol"
    assert payload["recorded_at"] == case.created_at
    assert payload["additive_suite_gap"] is True
    assert payload["existing_suite_caught"] is False


def test_suite_gap_register_ignores_unclassified_cases(tmp_path: Path):
    case = Case(id="case-2", mode=Mode.DETECTIVE)

    assert SuiteGapStore(tmp_path).save(case, model="stub") is None
    assert list(tmp_path.iterdir()) == []
