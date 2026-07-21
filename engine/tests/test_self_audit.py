from __future__ import annotations

from pathlib import Path

import pytest

from exhibit_a import EngineConfig, EvidenceEngine
from exhibit_a.cli import main
from exhibit_a.executor.base import ExecOutcome, ExecSpec, Executor, RepoState
from exhibit_a.executor.local_exec import LocalExecutor
from exhibit_a.hypothesis.generator import StubGenerator
from exhibit_a.models.case import Case, Mode, Verdict
from exhibit_a.studies.self_audit import (
    load_refactor_corpus,
    run_self_audit,
    wilson_interval,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
CORPUS = FIXTURES / "refactor_corpus"


def test_corpus_contains_three_real_behavior_preserving_refactor_categories():
    pairs = load_refactor_corpus(CORPUS)

    assert [pair.id for pair in pairs] == [
        "rename_helper",
        "extract_method",
        "loop_to_comprehension",
    ]
    assert {pair.category for pair in pairs} == {
        "rename",
        "extract_method",
        "loop_transform",
    }


def test_offline_self_audit_validates_corpus_and_reports_zero_false_convictions():
    def factory(_pair, _index):
        config = EngineConfig(
            reruns=1,
            max_refine=0,
            check_existing_suite=False,
            minimize_proven=False,
            score_evidence_strength=False,
        )
        return EvidenceEngine(StubGenerator(), LocalExecutor(), config), "offline-stub"

    report = run_self_audit(corpus_root=CORPUS, engine_factory=factory)

    assert report.overall.evaluated == 3
    assert report.overall.false_convictions == 0
    assert report.overall.rate == 0.0
    assert report.overall.lower_95 == 0.0
    assert report.overall.upper_95 == pytest.approx(0.561497, abs=1e-6)
    assert set(report.by_category) == {"rename", "extract_method", "loop_transform"}
    assert all(item.corpus_valid for item in report.items)
    assert {item.verdict for item in report.items} == {"INSUFFICIENT_EVIDENCE"}


class AlwaysPassExecutor(Executor):
    def __init__(self):
        self.closed = False

    def prepare(self, repo: RepoState) -> None:
        return None

    def run(self, repo: RepoState, spec: ExecSpec) -> ExecOutcome:
        return ExecOutcome(0, "1 passed", "")

    def close(self):
        self.closed = True


class FalseConvictionEngine:
    def __init__(self):
        self.executor = AlwaysPassExecutor()

    def investigate(self, claim, **kwargs):
        return Case(id="false-positive", mode=Mode.PROSECUTOR, verdict=Verdict.PROVEN)


def test_audit_counts_any_proven_case_on_an_innocent_pair_as_false_conviction(tmp_path: Path):
    manifest = CORPUS / "manifest.json"
    # Reuse one valid pair through a one-entry manifest rooted at a symlink-free copy.
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "base").mkdir()
    (root / "target").mkdir()
    (root / "base" / "app.py").write_text("VALUE = 1\n")
    (root / "target" / "app.py").write_text("VALUE = 1\n")
    (root / "contract.py").write_text("from app import VALUE\nassert VALUE == 1\n")
    (root / "manifest.json").write_text(
        '{"schema_version":"refactor-corpus/v1","cases":['
        '{"id":"innocent","category":"rename","claim":"claim",'
        '"base":"base","target":"target","contract":"contract.py"}]}'
    )
    engines: list[FalseConvictionEngine] = []

    def factory(_pair, _index):
        engine = FalseConvictionEngine()
        engines.append(engine)
        return engine, "test"

    report = run_self_audit(corpus_root=root, engine_factory=factory)

    assert manifest.is_file()  # the repository corpus remains untouched
    assert report.overall.evaluated == report.overall.false_convictions == 1
    assert report.overall.rate == 1.0
    assert report.items[0].false_conviction
    assert engines[0].executor.closed


def test_wilson_interval_rejects_invalid_counts_and_bounds_small_samples():
    assert wilson_interval(-1, 3) is None
    assert wilson_interval(4, 3) is None
    assert wilson_interval(0, 0) is None
    lower, upper = wilson_interval(1, 1) or (-1, -1)
    assert 0 < lower < upper <= 1


def test_corpus_loader_rejects_path_traversal(tmp_path: Path):
    (tmp_path / "manifest.json").write_text(
        '{"schema_version":"refactor-corpus/v1","cases":['
        '{"id":"escape","base":"../base","target":"target",'
        '"contract":"contract.py"}]}'
    )

    with pytest.raises(ValueError, match="unsafe refactor corpus path"):
        load_refactor_corpus(tmp_path)


def test_self_audit_cli_writes_rate_and_confidence_interval(tmp_path: Path, capsys):
    result = main(
        [
            "self-audit",
            str(CORPUS),
            "--offline",
            "--out",
            str(tmp_path),
        ]
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "false-conviction rate: 0% (0/3; 95% CI" in output
    assert len(list(tmp_path.glob("*.json"))) == 1
