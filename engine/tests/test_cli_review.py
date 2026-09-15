"""Reviewing a pull request inside an environment somebody else already built.

Detective mode against an arbitrary repository spends most of its budget rebuilding an
environment from a lockfile, and pilot v8 lost ten of thirty instances there. A pull
request is reviewed where its own CI already runs, so the environment exists already.
This command's job is to use it and never to construct one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from exhibit_a import cli
from exhibit_a.executor.docker_exec import DEFAULT_IMAGE, DockerExecutor
from exhibit_a.executor.local_exec import LocalExecutor
from exhibit_a.models.case import Mode

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = cli.main(["review", *argv])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_an_environment_must_be_named_and_is_never_built(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    # Falling back to building one would reintroduce the exact cost this mode avoids,
    # and would do it silently.
    common = [
        str(FIXTURES / "buggy_inventory"),
        "--base",
        str(FIXTURES / "fixed_inventory"),
        "--claim",
        "refactor the lookup",
        "--out",
        str(tmp_path / "reviews"),
    ]
    code, output = _run(capsys, *common)
    assert code == 2
    assert "never builds one" in output

    code, output = _run(capsys, *common, "--no-sandbox", "--image", "prebuilt:1")
    assert code == 2
    assert "never builds one" in output


def test_a_named_image_reaches_the_executor_unbuilt():
    # DockerExecutor.prepare returns any base image that is not its own default without
    # building anything, which is the whole mechanism behind reviewing in place.
    engine = cli._build_engine(use_docker=True, offline=True, base_image="prebuilt:1")

    assert isinstance(engine.executor, DockerExecutor)
    assert engine.executor.base_image == "prebuilt:1"
    assert engine.executor.prepare(object()) == "prebuilt:1"  # type: ignore[arg-type]


def test_omitting_an_image_leaves_the_ordinary_building_executor():
    engine = cli._build_engine(use_docker=True, offline=True)

    assert engine.executor.base_image == DEFAULT_IMAGE


def test_the_host_option_reviews_in_the_running_job(capsys: pytest.CaptureFixture[str]):
    engine = cli._build_engine(use_docker=False, offline=True)

    assert isinstance(engine.executor, LocalExecutor)


def test_a_review_needs_something_to_review_against(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    code, output = _run(
        capsys,
        str(FIXTURES / "buggy_inventory"),
        "--claim",
        "refactor the lookup",
        "--no-sandbox",
        "--out",
        str(tmp_path / "reviews"),
    )

    assert code == 2
    assert "--base" in output


def test_a_review_needs_a_claim(capsys: pytest.CaptureFixture[str], tmp_path: Path):
    code, output = _run(
        capsys,
        str(FIXTURES / "buggy_inventory"),
        "--base",
        str(FIXTURES / "fixed_inventory"),
        "--no-sandbox",
        "--out",
        str(tmp_path / "reviews"),
    )

    assert code == 2
    assert "--claim" in output


def test_a_local_review_runs_in_prosecutor_mode_and_stays_silent_without_proof(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    # The stub proposer cannot clear the gate, so this is the silence path: a verdict, a
    # stated reason, and nothing that could be mistaken for a finding.
    out = tmp_path / "reviews"
    code, output = _run(
        capsys,
        str(FIXTURES / "buggy_inventory"),
        "--base",
        str(FIXTURES / "fixed_inventory"),
        "--claim",
        "refactor the stock lookup",
        "--no-sandbox",
        "--offline",
        "--json",
        "--out",
        str(out),
    )

    assert code == 1, output
    import json

    case = json.loads(output[output.index("{") :])
    assert case["mode"] == Mode.PROSECUTOR.value
    assert case["verdict"] == "UNCERTAIN"
    assert case["silence_reason"]
    assert case["truth"]["execution"] == "COMPLETED"


def _proven_case():
    from exhibit_a.models.case import Case, Evidence, Verdict
    from exhibit_a.models.case import TestArtifact as CaseTestArtifact

    return Case(
        id="c1",
        mode=Mode.PROSECUTOR,
        verdict=Verdict.VERIFIED,
        claim_text="refactor the stock lookup",
        run_command="python3 -m pytest -x -q test_repro.py",
        test_file=CaseTestArtifact(path="test_repro.py", code="def test_x():\n    assert True\n"),
        evidence=Evidence(
            fail_log="E   KeyError: 'x'",
            fail_signature="KeyError: 'x'",
            pass_log="1 passed",
            reruns=5,
            deterministic=True,
        ),
    )


class _ProvenEngine:
    executor = None
    generator = None

    def investigate(self, *args, **kwargs):
        return _proven_case()


def test_a_proven_review_prints_the_comment_on_stdout(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # stdout carries the comment and nothing else, so a caller can pipe it straight into
    # a pull request without parsing anything first.
    monkeypatch.setattr(cli, "_build_engine", lambda **kwargs: _ProvenEngine())
    comment_out = tmp_path / "comment.md"

    code, _ = _run(
        capsys,
        str(FIXTURES / "buggy_inventory"),
        "--base",
        str(FIXTURES / "fixed_inventory"),
        "--claim",
        "refactor the stock lookup",
        "--no-sandbox",
        "--out",
        str(tmp_path / "reviews"),
        "--comment-out",
        str(comment_out),
    )

    assert code == 0
    written = comment_out.read_text()
    assert "proven regression" in written
    assert "python3 -m pytest -x -q test_repro.py" in written


def test_silence_writes_no_comment_file_at_all(capsys: pytest.CaptureFixture[str], tmp_path: Path):
    # Not an empty file. A caller testing for existence must not find one.
    comment_out = tmp_path / "comment.md"

    code, _ = _run(
        capsys,
        str(FIXTURES / "buggy_inventory"),
        "--base",
        str(FIXTURES / "fixed_inventory"),
        "--claim",
        "refactor the stock lookup",
        "--no-sandbox",
        "--offline",
        "--out",
        str(tmp_path / "reviews"),
        "--comment-out",
        str(comment_out),
    )

    assert code == 1
    assert not comment_out.exists()


def test_a_symlink_cannot_fetch_a_host_file_into_the_sandbox(tmp_path: Path):
    """The repository under test is assumed hostile, so it must not name a host file.

    `shutil.copytree` dereferences by default, so a checkout containing
    `innocent.txt -> ~/.codex/auth.json` had that file's contents copied into the tree the
    contributed test then reads. Inside a container that is a credential the sandbox
    fetched on the contribution's behalf.
    """
    from exhibit_a.executor.base import copy_for_sandbox

    secret = tmp_path / "host-secret.txt"
    secret.write_text("PRETEND-CREDENTIAL-VALUE")
    src = tmp_path / "checkout"
    src.mkdir()
    (src / "mod.py").write_text("VALUE = 1\n")
    (src / "escape.txt").symlink_to(secret)
    (src / "inside.txt").write_text("ok\n")
    (src / "ok-link").symlink_to(src / "inside.txt")

    work = tmp_path / "work"
    copy_for_sandbox(src, work)

    assert not (work / "escape.txt").exists()
    assert "PRETEND-CREDENTIAL-VALUE" not in "".join(
        p.read_text() for p in work.rglob("*") if p.is_file() and not p.is_symlink()
    )
    # A link that stays inside the checkout is part of the tree and is preserved as a link.
    assert (work / "ok-link").is_symlink()
    assert (work / "mod.py").is_file()


def test_our_own_runtime_output_is_never_copied_into_the_sandbox(tmp_path: Path):
    """Prosecutor mode reviews a working directory, not a fresh clone.

    `.exhibit-a` is where this tool writes its own output, and in a repository it has
    been run in that directory holds study caches measured in gigabytes. Copying our
    scratch into the sandbox is slow at best; here it aborted the run outright, because
    a cached clone inside it contained a dangling symlink.
    """
    from exhibit_a.executor.base import copy_for_sandbox

    src = tmp_path / "checkout"
    (src / ".exhibit-a" / "research").mkdir(parents=True)
    (src / ".exhibit-a" / "research" / "big.json").write_text("{}")
    (src / "__pycache__").mkdir()
    (src / "mod.py").write_text("VALUE = 1\n")
    (src / "dangling").symlink_to(tmp_path / "gone")
    nested = src / "vendor" / ".exhibit-a"
    nested.mkdir(parents=True)
    (nested / "tracked.py").write_text("Y = 2\n")

    work = tmp_path / "work"
    copy_for_sandbox(src, work)

    assert (work / "mod.py").is_file()
    assert not (work / ".exhibit-a").exists()
    assert not (work / "__pycache__").exists()
    # Only ours, and only at the root. Reserving the name throughout a third-party tree
    # would silently delete a tracked directory from the revision under test.
    assert (work / "vendor" / ".exhibit-a" / "tracked.py").is_file()
    # A broken symlink in someone else's tree is not a reason to abandon their review.
    assert not (work / "dangling").exists()


def test_a_supplied_image_is_used_even_when_it_is_the_default_name(tmp_path: Path):
    # The default image name used to double as the signal for "build one", so naming it
    # explicitly meant review built an environment while claiming it never does.
    from exhibit_a.executor.docker_exec import DEFAULT_IMAGE

    engine = cli._build_engine(use_docker=True, offline=True, base_image=DEFAULT_IMAGE)

    assert engine.executor.prebuilt is True
    assert engine.executor.prepare(object()) == DEFAULT_IMAGE  # type: ignore[arg-type]
