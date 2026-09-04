from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from exhibit_a.studies.fix_corpus import _date, _is_production_python, _top_repositories
from exhibit_a.studies.fix_coverage import (
    CORPUS_SCHEMA,
    FixCorpus,
    FixInstance,
    RunConfig,
    WorkerResult,
    classify_worker_result,
    load_fix_corpus,
    run_fix_coverage_study,
)


def _instance(identifier: str = "owner-repo-pr-1") -> FixInstance:
    return FixInstance(
        id=identifier,
        repository="https://github.com/owner/repo.git",
        buggy_sha="1" * 40,
        fix_sha="2" * 40,
        claim="Fix a deterministic bug",
        commit_date="2026-04-01T12:00:00+00:00",
        source_url="https://github.com/owner/repo/pull/1",
    )


def _config() -> RunConfig:
    return RunConfig(
        requested_model="gpt-5.6-sol",
        provider_config=None,
        instance_timeout_s=10,
        total_ceiling_s=30,
        execution_timeout_s=5,
        reruns=5,
        max_refine=3,
    )


def _result(
    *,
    case: dict | None,
    error: str | None = None,
    timed_out: bool = False,
) -> WorkerResult:
    return WorkerResult(
        instance_id="owner-repo-pr-1",
        started_at="2026-09-04T00:00:00+00:00",
        finished_at="2026-09-04T00:00:01+00:00",
        wall_time_s=1.0,
        case=case,
        error_type="Error" if error else None,
        error=error,
        timed_out=timed_out,
    )


def _uncertain(**updates: object) -> dict:
    case = {
        "verdict": "UNCERTAIN",
        "silence_reason": "no candidate produced a deterministic flip",
        "hypotheses": [],
        "proposal_runs": [],
        "existing_suite_passed": True,
        "existing_suite_log": "",
        "evidence": {"fail_log": "", "pass_log": "", "runs": []},
    }
    case.update(updates)
    return case


def test_load_fix_corpus_validates_and_hashes_manifest(tmp_path: Path) -> None:
    item = _instance()
    payload = {
        "schema_version": CORPUS_SCHEMA,
        "preregistration": {"path": "preregistration.json", "sha256": "a" * 64},
        "selection": {"rule": "mechanical"},
        "exclusions": [{"reason_code": "no_production_python_change"}],
        "instances": [item.__dict__],
    }
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(payload))

    corpus = load_fix_corpus(path)

    assert corpus.instances == (item,)
    assert len(corpus.sha256) == 64
    payload["instances"].append(item.__dict__)
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="duplicate corpus instance"):
        load_fix_corpus(path)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (_result(case=None, error="deadline", timed_out=True), "timed_out"),
        (_result(case=None, error="git clone failed"), "checkout_failed"),
        (
            _result(
                case=_uncertain(
                    silence_reason=(
                        "could not build environment: no poetry.lock, Pipfile.lock, "
                        "or requirements*.txt was found"
                    )
                )
            ),
            "environment_no_supported_lockfile",
        ),
        (
            _result(
                case=_uncertain(
                    silence_reason=(
                        "could not build environment: pinned dependency image failed to build"
                    )
                )
            ),
            "environment_dependency_install_failed",
        ),
        (
            _result(
                case=_uncertain(
                    existing_suite_passed=False,
                    existing_suite_log="postgres connection refused",
                )
            ),
            "suite_requires_unavailable_services",
        ),
        (_result(case=_uncertain()), "no_candidate_proposed"),
        (
            _result(case=_uncertain(hypotheses=[{"reason": "flaky on target: failed 2/5 reruns"}])),
            "candidate_flaky",
        ),
        (
            _result(case=_uncertain(hypotheses=[{"reason": "harness-tamper: writes source"}])),
            "candidate_tamper",
        ),
        (
            _result(case=_uncertain(hypotheses=[{"reason": "test imports nothing"}])),
            "candidate_vacuous",
        ),
        (
            _result(
                case=_uncertain(hypotheses=[{"reason": "failed for the wrong reason: expected X"}])
            ),
            "candidate_wrong_failure_signature",
        ),
        (
            _result(
                case=_uncertain(
                    hypotheses=[{"reason": "target failed for an environmental/harness reason"}]
                )
            ),
            "candidate_infrastructure_failure",
        ),
        (
            _result(
                case=_uncertain(
                    hypotheses=[{"reason": "test does not fail on the target (buggy) state"}]
                )
            ),
            "candidate_did_not_fail_on_buggy",
        ),
        (
            _result(
                case=_uncertain(
                    hypotheses=[{"reason": "test does not pass on the base/fixed state"}]
                )
            ),
            "candidate_failed_on_fixed",
        ),
    ],
)
def test_failure_taxonomy(result: WorkerResult, expected: str) -> None:
    assert classify_worker_result(result)[0] == expected


def test_study_keeps_partial_separate_and_resumes(tmp_path: Path) -> None:
    instances = (_instance(), replace(_instance(), id="owner-repo-pr-2", fix_sha="3" * 40))
    corpus = FixCorpus(
        path=str(tmp_path / "corpus.json"),
        sha256="f" * 64,
        preregistration={"path": "preregistration.json"},
        selection={"rule": "mechanical"},
        exclusions=({"reason_code": "no_production_python_change"},),
        instances=instances,
    )
    calls = []

    def runner(instance: FixInstance, root: Path, timeout_s: float) -> WorkerResult:
        calls.append((instance.id, timeout_s))
        verdict = "VERIFIED" if len(calls) == 1 else "PARTIAL"
        return replace(
            _result(
                case={
                    "verdict": verdict,
                    "hypotheses": [{"reason": None}],
                    "proposal_runs": [
                        {
                            "provider": "codex_cli",
                            "requested_model": "gpt-5.6-sol",
                            "confirmed_model": "unknown_cli_no_telemetry",
                            "confirmed_version": "unknown_cli_no_telemetry",
                            "input_tokens": None,
                            "output_tokens": None,
                            "total_tokens": None,
                            "cost_usd": None,
                        }
                    ],
                }
            ),
            instance_id=instance.id,
        )

    report = run_fix_coverage_study(
        corpus=corpus,
        output_root=tmp_path / "run",
        config=_config(),
        runner=runner,
    )
    resumed = run_fix_coverage_study(
        corpus=corpus,
        output_root=tmp_path / "run",
        config=_config(),
        runner=runner,
    )

    assert len(calls) == 2
    assert report["headline"]["verified"] == 1
    assert report["headline"]["partial"] == 1
    assert report["headline"]["denominator"] == 2
    assert report["selection"]["screened_candidates"] == 3
    assert report["model_telemetry"]["actual_model_spend_usd"] is None
    assert "unavailable" in report["model_telemetry"]["spend_basis"]
    assert resumed["completed_instances"] == 2


def test_resume_rejects_parameter_drift(tmp_path: Path) -> None:
    corpus = FixCorpus(
        path=str(tmp_path / "corpus.json"),
        sha256="f" * 64,
        preregistration={},
        selection={},
        exclusions=(),
        instances=(_instance(),),
    )
    run_fix_coverage_study(
        corpus=corpus,
        output_root=tmp_path / "run",
        config=_config(),
        runner=lambda instance, root, timeout: _result(case=_uncertain()),
    )

    with pytest.raises(ValueError, match="different run parameters"):
        run_fix_coverage_study(
            corpus=corpus,
            output_root=tmp_path / "run",
            config=replace(_config(), reruns=4),
            runner=lambda instance, root, timeout: _result(case=_uncertain()),
        )


def test_selector_distinguishes_production_paths_and_inclusive_end_date() -> None:
    assert _is_production_python("src/package/runtime.py")
    assert not _is_production_python("tests/test_runtime.py")
    assert not _is_production_python("docs/conf.py")
    assert _date("2026-08-31", end_of_day=True).hour == 23


def test_repository_search_paginates_in_stable_star_order() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls = []

        def get(self, url: str) -> dict:
            self.calls.append(url)
            page = len(self.calls)
            size = 100 if page == 1 else 20
            offset = (page - 1) * 100
            return {"items": [{"rank": offset + index} for index in range(size)]}

    client = Client()
    repositories = _top_repositories(client, 120)

    assert [item["rank"] for item in repositories] == list(range(120))
    assert len(client.calls) == 2
    assert "page=1" in client.calls[0]
    assert "page=2" in client.calls[1]
