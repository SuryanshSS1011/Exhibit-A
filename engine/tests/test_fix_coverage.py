from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from exhibit_a.studies import fix_corpus
from exhibit_a.studies.fix_corpus import (
    GitHubClient,
    _candidate_prs,
    _date,
    _is_production_python,
    _top_repositories,
)
from exhibit_a.studies.fix_coverage import (
    CORPUS_SCHEMA,
    FixCorpus,
    FixInstance,
    RunConfig,
    WorkerResult,
    classify_worker_result,
    create_public_fix_coverage_report,
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
            _result(
                case=_uncertain(
                    silence_reason="Codex generation failed: You've hit your usage limit"
                )
            ),
            "provider_quota_exhausted",
        ),
        (
            _result(case=_uncertain(silence_reason="Codex generation failed: invalid output")),
            "provider_generation_failed",
        ),
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
                    "evidence": {"fail_log": "PRIVATE EXECUTION LOG"},
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

    public = create_public_fix_coverage_report(
        corpus=corpus,
        private_root=tmp_path / "run",
        output=tmp_path / "public.json",
        execution_source_revision="a" * 40,
    )
    assert public["headline"]["verified"] == 1
    assert public["execution_source_revision"] == "a" * 40
    public_taxonomy = {
        item["category"]: item["count"] for item in public["refined_failure_taxonomy"]
    }
    assert public_taxonomy["candidate_flaky"] == 0
    assert public_taxonomy["timed_out"] == 0
    assert "PRIVATE EXECUTION LOG" not in json.dumps(public)
    assert corpus.path not in json.dumps(public)


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


def test_candidate_rule_accepts_exact_bug_label_or_fix_title() -> None:
    class Client:
        def get(self, url: str) -> dict:
            assert "label%3Abug" not in url
            return {
                "items": [
                    {
                        "number": 1,
                        "title": "Feature: add a mode",
                        "html_url": "https://example.test/1",
                        "labels": [{"name": "bug"}],
                        "pull_request": {"merged_at": "2026-03-03T00:00:00Z"},
                    },
                    {
                        "number": 2,
                        "title": "Fixed parser bounds",
                        "html_url": "https://example.test/2",
                        "labels": [],
                        "pull_request": {"merged_at": "2026-03-02T00:00:00Z"},
                    },
                    {
                        "number": 3,
                        "title": "Prefix cache keys",
                        "html_url": "https://example.test/3",
                        "labels": [],
                        "pull_request": {"merged_at": "2026-03-01T00:00:00Z"},
                    },
                ]
            }

    candidates = _candidate_prs(Client(), "owner/repo", "2026-02-17", "2026-08-31")

    assert [candidate["number"] for candidate in candidates] == [2, 1]
    assert candidates[0]["selection_basis"] == ["fix_title_prefix"]
    assert candidates[1]["selection_basis"] == ["exact_bug_label"]


def test_github_client_retries_transient_transport_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    attempts = 0
    sleeps = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok": true}'

    def open_request(request: object, timeout: int) -> Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise fix_corpus.urllib.error.URLError("no route to host")
        return Response()

    monkeypatch.setattr(fix_corpus.urllib.request, "urlopen", open_request)
    monkeypatch.setattr(fix_corpus.time, "sleep", sleeps.append)

    assert GitHubClient(tmp_path).get("https://api.github.test/data") == {"ok": True}
    assert attempts == 3
    assert sleeps == [1, 2]


def _corpus_of(tmp_path: Path, count: int) -> FixCorpus:
    return FixCorpus(
        path=str(tmp_path / "corpus.json"),
        sha256="f" * 64,
        preregistration={"path": "preregistration.json"},
        selection={"rule": "mechanical"},
        exclusions=(),
        instances=tuple(
            replace(_instance(), id=f"owner-repo-pr-{n}", fix_sha=f"{n:040d}")
            for n in range(1, count + 1)
        ),
    )


def _quota_exhausted() -> dict:
    """The shape the real pilot saw: no hypothesis at all, and a usage-limit silence."""
    return {
        "verdict": "UNCERTAIN",
        "hypotheses": [],
        "silence_reason": "Codex generation failed: usage limit reached",
        "evidence": {},
        "proposal_runs": [],
    }


def _silent(reason: str) -> dict:
    return {
        "verdict": "UNCERTAIN",
        "hypotheses": [{"reason": reason}],
        "evidence": {},
        "proposal_runs": [],
    }


def test_judged_denominator_separates_the_judge_from_the_plumbing(tmp_path: Path) -> None:
    """A low headline means nothing until you know whether the judge ever ruled.

    Two instances are judged -- one verified, one rejected for not failing on the buggy
    state -- and two never produce a classified candidate. The headline is 1/4; the judge
    ruled twice and was right once.
    """
    corpus = _corpus_of(tmp_path, 4)
    scripted = [
        {
            "verdict": "VERIFIED",
            "hypotheses": [{"reason": None}],
            "evidence": {},
            "proposal_runs": [],
        },
        _silent("test does not fail on the target (buggy) state"),
        _silent("pinned dependency image failed to build: could not install numpy"),
        _quota_exhausted(),
    ]
    calls = []

    def runner(instance: FixInstance, root: Path, timeout_s: float) -> WorkerResult:
        case = scripted[len(calls)]
        calls.append(instance.id)
        return replace(_result(case=case), instance_id=instance.id)

    report = run_fix_coverage_study(
        corpus=corpus, output_root=tmp_path / "run", config=_config(), runner=runner
    )
    headline = report["headline"]

    assert headline["denominator"] == 4
    assert headline["verified"] == 1
    assert headline["judged_denominator"] == 2
    assert headline["reached_judge_fraction"] == 0.5
    assert headline["verified_fraction_of_judged"] == 0.5


def test_an_unclassified_rejection_is_not_counted_as_judged(tmp_path: Path) -> None:
    """The catch-all means the reason was not understood, which is not proof of a ruling."""
    corpus = _corpus_of(tmp_path, 2)
    scripted = [
        {
            "verdict": "VERIFIED",
            "hypotheses": [{"reason": None}],
            "evidence": {},
            "proposal_runs": [],
        },
        _silent("something the classifier has never seen before"),
    ]
    calls = []

    def runner(instance: FixInstance, root: Path, timeout_s: float) -> WorkerResult:
        case = scripted[len(calls)]
        calls.append(instance.id)
        return replace(_result(case=case), instance_id=instance.id)

    report = run_fix_coverage_study(
        corpus=corpus, output_root=tmp_path / "run", config=_config(), runner=runner
    )
    headline = report["headline"]

    assert headline["judged_denominator"] == 1
    assert headline["unclassified"] == 1, "the catch-all must stay visible, not be absorbed"


def test_a_rejected_candidate_counts_as_judged(tmp_path: Path) -> None:
    """A candidate the judge refused is evidence the judge worked, not that it failed."""
    corpus = _corpus_of(tmp_path, 2)
    scripted = [
        {
            "verdict": "VERIFIED",
            "hypotheses": [{"reason": None}],
            "evidence": {},
            "proposal_runs": [],
        },
        _silent("test does not fail on the target (buggy) state"),
    ]
    calls = []

    def runner(instance: FixInstance, root: Path, timeout_s: float) -> WorkerResult:
        case = scripted[len(calls)]
        calls.append(instance.id)
        return replace(_result(case=case), instance_id=instance.id)

    report = run_fix_coverage_study(
        corpus=corpus, output_root=tmp_path / "run", config=_config(), runner=runner
    )
    headline = report["headline"]

    assert headline["judged_denominator"] == 2, "both instances produced a ruling"
    assert headline["reached_judge_fraction"] == 1.0
    assert headline["verified_fraction_of_judged"] == 0.5


def test_quota_exhaustion_halts_instead_of_spending_the_corpus(tmp_path: Path) -> None:
    """A throttled run must not masquerade as a measurement of the engine.

    Recording a quota failure would consume a corpus row and put it in the denominator,
    which is exactly how pilot v4 came to report six instances the provider never answered.
    """
    corpus = _corpus_of(tmp_path, 4)
    scripted = [
        {
            "verdict": "VERIFIED",
            "hypotheses": [{"reason": None}],
            "evidence": {},
            "proposal_runs": [],
        },
        _quota_exhausted(),
    ]
    calls = []

    def runner(instance: FixInstance, root: Path, timeout_s: float) -> WorkerResult:
        case = scripted[min(len(calls), len(scripted) - 1)]
        calls.append(instance.id)
        return replace(_result(case=case), instance_id=instance.id)

    report = run_fix_coverage_study(
        corpus=corpus, output_root=tmp_path / "run", config=_config(), runner=runner
    )

    assert len(calls) == 2, "the run stops at the quota wall rather than burning instances"
    assert report["halted_reason"] == "provider_quota_exhausted"
    assert report["complete"] is False
    assert report["completed_instances"] == 1, "the quota instance is not checkpointed"
    assert report["headline"]["denominator"] == 4
    assert report["headline"]["provider_unavailable"] == 0


def test_resuming_after_a_quota_halt_retries_the_unattempted_instance(tmp_path: Path) -> None:
    corpus = _corpus_of(tmp_path, 3)
    quota_until = {"calls": 0}

    def runner(instance: FixInstance, root: Path, timeout_s: float) -> WorkerResult:
        quota_until["calls"] += 1
        exhausted = quota_until["calls"] <= 1
        case = (
            _quota_exhausted()
            if exhausted
            else {
                "verdict": "VERIFIED",
                "hypotheses": [{"reason": None}],
                "evidence": {},
                "proposal_runs": [],
            }
        )
        return replace(_result(case=case), instance_id=instance.id)

    first = run_fix_coverage_study(
        corpus=corpus, output_root=tmp_path / "run", config=_config(), runner=runner
    )
    assert first["completed_instances"] == 0
    assert first["halted_reason"] == "provider_quota_exhausted"

    resumed = run_fix_coverage_study(
        corpus=corpus, output_root=tmp_path / "run", config=_config(), runner=runner
    )

    assert resumed["halted_reason"] is None
    assert resumed["complete"] is True
    assert resumed["completed_instances"] == 3, "every instance is measured after the reset"
    assert resumed["headline"]["verified"] == 3
