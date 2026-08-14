from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import ClassVar

import pytest

from exhibit_a import cli as cli_module
from exhibit_a import eef
from exhibit_a.cli import main
from exhibit_a.eef import verify_bundle
from exhibit_a.executor.base import ExecOutcome, ExecSpec, Executor, RepoState

KEY = b"cli-refactor-evidence-key-at-least-32-bytes"
CONTRACT = "def test_contract():\n    assert True\n"


class FakeDockerExecutor(Executor):
    source_access = "disposable_copy"
    network_access = "disabled"
    isolation = "container"
    credential_access = "none"

    target_outcome = ExecOutcome(0, "1 passed", "")
    instances: ClassVar[list[FakeDockerExecutor]] = []

    def __init__(self, root: str | Path, contract_code: str):
        self.root = Path(root)
        self.contract_code = contract_code
        self.closed = False
        self.runs: list[tuple[RepoState, ExecSpec]] = []
        self.instances.append(self)

    def prepare(self, repo: RepoState) -> None:
        return None

    def run(self, repo: RepoState, spec: ExecSpec) -> ExecOutcome:
        self.runs.append((repo, spec))
        if repo.label == "target":
            return self.target_outcome
        return ExecOutcome(0, "1 passed", "")

    def close(self) -> None:
        self.closed = True


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    base = tmp_path / "base"
    target = tmp_path / "target"
    base.mkdir()
    target.mkdir()
    (base / "behavior.py").write_text("VALUE = 10\n")
    (target / "behavior.py").write_text("VALUE = 10\n")
    contract = tmp_path / "contract.py"
    contract.write_text(CONTRACT)
    key = tmp_path / "eef.key"
    key.write_bytes(KEY)
    return base, target, contract, key


def _argv(tmp_path: Path) -> tuple[list[str], Path]:
    base, target, contract, key = _inputs(tmp_path)
    output = tmp_path / "refactor.eef"
    return (
        [
            "refactor-bundle",
            "--base-source",
            str(base),
            "--target-source",
            str(target),
            "--contract",
            str(contract),
            "--signing-key",
            str(key),
            "--out",
            str(output),
        ],
        output,
    )


@pytest.mark.parametrize(
    ("target_outcome", "verdict"),
    [
        (ExecOutcome(0, "1 passed", ""), "VERIFIED"),
        (ExecOutcome(1, "", "E   AssertionError: assert 12 == 10"), "FAILED"),
    ],
)
def test_refactor_bundle_cli_collects_and_mints_signed_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    target_outcome: ExecOutcome,
    verdict: str,
):
    argv, output = _argv(tmp_path)
    FakeDockerExecutor.instances.clear()
    FakeDockerExecutor.target_outcome = target_outcome
    monkeypatch.setattr(cli_module, "RefactorReplayExecutor", FakeDockerExecutor)

    assert main(argv) == 0

    assert capsys.readouterr().out.strip() == str(output)
    assert output.is_file()
    executor = FakeDockerExecutor.instances[-1]
    assert executor.closed
    assert len(executor.runs) == 6
    assert all(not spec.network for _, spec in executor.runs)
    assert {repo.label for repo, _ in executor.runs} == {"base", "target"}
    assert all("exhibit-a-refactor-input-" in repo.path for repo, _ in executor.runs)
    assert all(not Path(repo.path).exists() for repo, _ in executor.runs)
    with zipfile.ZipFile(output) as archive:
        evidence = json.loads(archive.read("refactor.json"))
        assert archive.read("sources/base/behavior.py") == b"VALUE = 10\n"
        assert archive.read("sources/target/behavior.py") == b"VALUE = 10\n"
    assert evidence["result"]["verdict"] == verdict
    assert verify_bundle(output, signing_key=KEY).signature_verified


@pytest.mark.parametrize(("flag", "value"), [("--reruns", "1"), ("--timeout", "121")])
def test_refactor_bundle_cli_rejects_invalid_budgets_before_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    flag: str,
    value: str,
):
    argv, output = _argv(tmp_path)

    class UnexpectedExecutor:
        def __init__(self, root, contract_code):
            pytest.fail("executor should not be initialized")

    monkeypatch.setattr(cli_module, "RefactorReplayExecutor", UnexpectedExecutor)

    assert main([*argv, flag, value]) == 2
    assert flag in capsys.readouterr().err
    assert not output.exists()


def test_refactor_bundle_cli_closes_executor_after_collection_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    argv, output = _argv(tmp_path)

    class FailingExecutor(FakeDockerExecutor):
        def run(self, repo: RepoState, spec: ExecSpec) -> ExecOutcome:
            raise RuntimeError("sandbox failed")

    FailingExecutor.instances.clear()
    monkeypatch.setattr(cli_module, "RefactorReplayExecutor", FailingExecutor)

    assert main(argv) == 2
    assert "sandbox failed" in capsys.readouterr().err
    assert FailingExecutor.instances[-1].closed
    assert not output.exists()


def test_refactor_bundle_cli_rejects_short_key_before_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    argv, output = _argv(tmp_path)
    key_index = argv.index("--signing-key") + 1
    Path(argv[key_index]).write_bytes(b"too short")

    class UnexpectedExecutor:
        def __init__(self, root, contract_code):
            pytest.fail("executor should not be initialized")

    monkeypatch.setattr(cli_module, "RefactorReplayExecutor", UnexpectedExecutor)

    assert main(argv) == 2
    assert "at least 32 bytes" in capsys.readouterr().err
    assert not output.exists()


def test_refactor_bundle_cli_rejects_symlinked_sources_before_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    argv, output = _argv(tmp_path)
    base = Path(argv[argv.index("--base-source") + 1])
    (base / "linked.py").symlink_to(base / "behavior.py")

    class UnexpectedExecutor:
        def __init__(self, root, contract_code):
            pytest.fail("executor should not be initialized")

    monkeypatch.setattr(cli_module, "RefactorReplayExecutor", UnexpectedExecutor)

    assert main(argv) == 2
    assert "cannot contain symlinks" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize(
    "condition",
    [
        "key-in-source",
        "output-is-key",
        "contract-is-key",
        "output-is-contract",
        "output-in-source",
        "same-source",
        "nested-source",
    ],
)
def test_refactor_bundle_cli_rejects_unsafe_path_relationships_before_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    condition: str,
):
    argv, output = _argv(tmp_path)
    base = Path(argv[argv.index("--base-source") + 1])
    key_index = argv.index("--signing-key") + 1
    output_index = argv.index("--out") + 1
    if condition == "key-in-source":
        source_key = base / "eef.key"
        source_key.write_bytes(KEY)
        argv[key_index] = str(source_key)
    elif condition == "output-is-key":
        argv[output_index] = argv[key_index]
    elif condition == "contract-is-key":
        argv[argv.index("--contract") + 1] = argv[key_index]
    elif condition == "output-is-contract":
        argv[output_index] = argv[argv.index("--contract") + 1]
    elif condition == "output-in-source":
        argv[output_index] = str(base / "refactor.eef")
    elif condition == "same-source":
        argv[argv.index("--target-source") + 1] = str(base)
    else:
        nested = base / "nested-target"
        nested.mkdir()
        argv[argv.index("--target-source") + 1] = str(nested)

    class UnexpectedExecutor:
        def __init__(self, root, contract_code):
            pytest.fail("executor should not be initialized")

    monkeypatch.setattr(cli_module, "RefactorReplayExecutor", UnexpectedExecutor)

    assert main(argv) == 2
    assert capsys.readouterr().err.startswith("error: cannot create refactor EEF bundle:")
    assert not output.exists()
    assert Path(argv[key_index]).read_bytes() == KEY
    if condition == "output-is-contract":
        assert Path(argv[argv.index("--contract") + 1]).read_text() == CONTRACT


def test_refactor_bundle_cli_enforces_combined_source_budget_before_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    argv, output = _argv(tmp_path)

    class UnexpectedExecutor:
        def __init__(self, root, contract_code):
            pytest.fail("executor should not be initialized")

    monkeypatch.setattr(cli_module, "RefactorReplayExecutor", UnexpectedExecutor)
    monkeypatch.setattr(eef, "_MAX_ARCHIVE_ENTRIES", 8)

    assert main(argv) == 2
    assert "too many entries" in capsys.readouterr().err
    assert not output.exists()


def test_source_snapshot_traversal_bounds_empty_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    source = tmp_path / "source"
    source.mkdir()
    for index in range(4):
        (source / f"empty-{index}").mkdir()
    monkeypatch.setattr(eef, "_MAX_ARCHIVE_ENTRIES", 3)

    with pytest.raises(ValueError, match="too many entries"):
        eef.materialize_source_snapshot(source, tmp_path / "snapshot")
