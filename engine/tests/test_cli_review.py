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
