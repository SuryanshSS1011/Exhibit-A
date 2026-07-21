from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from exhibit_a.eef import create_bundle, verify_bundle
from exhibit_a.models.case import Case, Evidence, Mode, TestArtifact as CaseTestArtifact, Verdict

KEY = b"evidence-publisher-test-key-32-bytes!!"
TEST_CODE = (
    "from inventory import stock_for\n\n"
    "def test_unknown_sku():\n"
    "    assert stock_for([], 'missing') == 0\n"
)


def _case() -> dict:
    case = Case(id="eef-fixture", mode=Mode.DETECTIVE)
    case.created_at = "2026-07-21T00:00:00+00:00"
    case.verdict = Verdict.PROVEN
    case.test_file = CaseTestArtifact("test_repro.py", TEST_CODE)
    case.run_command = "python3 -m pytest -x -q test_repro.py"
    case.evidence = Evidence(
        fail_log="E   AssertionError: wrong value",
        fail_signature="AssertionError: wrong value",
        pass_log="1 passed",
        reruns=2,
        deterministic=True,
    )
    return case.to_dict()


def test_eef_is_byte_deterministic_and_verifies_offline(tmp_path: Path):
    target = tmp_path / "target"
    base = tmp_path / "base"
    target.mkdir()
    base.mkdir()
    (target / "inventory.py").write_text("def stock_for(rows, sku): return 1\n")
    (base / "inventory.py").write_text("def stock_for(rows, sku): return 0\n")
    (target / ".env").write_text("SECRET=excluded\n")

    first = create_bundle(
        _case(),
        tmp_path / "first.eef",
        target_source=target,
        base_source=base,
        signing_key=KEY,
    )
    second = create_bundle(
        _case(),
        tmp_path / "second.eef",
        target_source=target,
        base_source=base,
        signing_key=KEY,
    )

    assert first.read_bytes() == second.read_bytes()
    result = verify_bundle(first, signing_key=KEY)
    assert result.integrity_verified
    assert result.signature_verified
    assert result.execution_verified is None
    with pytest.raises(ValueError, match="signature verification failed"):
        verify_bundle(first, signing_key=b"different-publisher-key-32-bytes!!!")


def test_eef_reexecution_uses_docker_argv_and_unchanged_flip_judge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    target = tmp_path / "target"
    base = tmp_path / "base"
    target.mkdir()
    base.mkdir()
    (target / "inventory.py").write_text("def stock_for(rows, sku): return 1\n")
    (base / "inventory.py").write_text("def stock_for(rows, sku): return 0\n")
    bundle = create_bundle(
        _case(),
        tmp_path / "case.eef",
        target_source=target,
        base_source=base,
        signing_key=KEY,
    )
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[1] == "build":
            return subprocess.CompletedProcess(argv, 0, "built", "")
        if any("-target" in arg for arg in argv):
            return subprocess.CompletedProcess(argv, 1, "", "E   AssertionError: wrong value")
        return subprocess.CompletedProcess(argv, 0, "1 passed", "")

    monkeypatch.setattr("exhibit_a.eef.subprocess.run", fake_run)

    result = verify_bundle(bundle, signing_key=KEY, execute=True)

    assert result.execution_verified is True
    assert all(isinstance(call, list) for call in calls)
    assert all(
        "--network" in call and call[call.index("--network") + 1] == "none" for call in calls
    )
    assert len([call for call in calls if call[1] == "run"]) == 3
