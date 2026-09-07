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
    _exclude_prior_candidates,
    _is_production_python,
    _load_exclusion_manifests,
    _top_repositories,
)
from exhibit_a.studies.fix_coverage import (
    CORPUS_SCHEMA,
    WORKER_SCHEMA,
    FixCorpus,
    FixInstance,
    RunConfig,
    WorkerResult,
    classify_environment_install_failure,
    _ID,
    _JUDGED_FAILURE_CATEGORIES,
    _validate_config,
    classify_worker_result,
    create_public_fix_coverage_report,
    load_fix_corpus,
    run_fix_coverage_study,
    _worker_attempt_history,
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
        (
            _result(case=_uncertain(existing_suite_passed=False)),
            "existing_suite_failed",
        ),
        (
            _result(
                case=_uncertain(
                    existing_suite_passed=None,
                    existing_suite_log="ERROR collecting tests/conftest.py",
                )
            ),
            "suite_infrastructure_failure",
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


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("package requires a different Python: 3.13", "python_version_incompatible"),
        ("No matching distribution found for pinned==1", "pinned_distribution_unavailable"),
        ("ResolutionImpossible: conflicting dependencies", "dependency_resolution_conflict"),
        ("THESE PACKAGES DO NOT MATCH THE HASHES", "artifact_hash_or_integrity_failure"),
        ("certificate verify failed while downloading", "package_index_or_network_failure"),
        ("Failed building wheel for native", "native_distribution_build_failure"),
        (
            "metadata-generation-failed: pyproject.toml did not run successfully",
            "package_build_backend_or_metadata_failure",
        ),
        ("dependency image failed for an unrecognized reason", "other_install_failure"),
    ],
)
def test_environment_install_failure_taxonomy(reason: str, expected: str) -> None:
    assert classify_environment_install_failure(reason) == expected


def test_study_keeps_partial_separate_and_resumes(tmp_path: Path) -> None:
    instances = (_instance(), replace(_instance(), id="owner-repo-pr-2", fix_sha="3" * 40))
    corpus = FixCorpus(
        path=str(tmp_path / "corpus.json"),
        sha256="f" * 64,
        preregistration={"path": "preregistration.json"},
        selection={"rule": "mechanical"},
        exclusions=(
            {"reason_code": "no_production_python_change"},
            {"reason_code": "prior_corpus_member"},
        ),
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
    assert report["selection"]["eligibility_exclusions"] == 1
    assert report["selection"]["prior_corpus_exclusions"] == 1
    assert report["selection"]["unselected_eligible_candidates"] == 0
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
    assert public["probe_only"] is False
    assert public["finished_at"] == "2026-09-04T00:00:01+00:00"
    assert public["execution_source_revision"] == "a" * 40
    assert public["selection"]["prior_corpus_exclusions"] == 1
    assert public["execution_source_revisions"] == ["a" * 40]
    assert public["execution_segments"] == [
        {
            "first_instance": 1,
            "last_instance": 2,
            "first_instance_id": "owner-repo-pr-1",
            "last_instance_id": "owner-repo-pr-2",
            "source_revision": "a" * 40,
        }
    ]
    public_taxonomy = {
        item["category"]: item["count"] for item in public["refined_failure_taxonomy"]
    }
    assert public_taxonomy["candidate_flaky"] == 0
    assert public_taxonomy["timed_out"] == 0
    assert "PRIVATE EXECUTION LOG" not in json.dumps(public)
    assert corpus.path not in json.dumps(public)


def test_report_breaks_down_dependency_install_failures_without_public_raw_reason(
    tmp_path: Path,
) -> None:
    corpus = FixCorpus(
        path=str(tmp_path / "corpus.json"),
        sha256="f" * 64,
        preregistration={},
        selection={},
        exclusions=(),
        instances=(_instance(),),
    )
    private_reason = (
        "could not build environment: pinned dependency image failed to build; "
        "No matching distribution found for private-package==1"
    )
    report = run_fix_coverage_study(
        corpus=corpus,
        output_root=tmp_path / "run",
        config=_config(),
        runner=lambda instance, root, timeout: _result(
            case=_uncertain(silence_reason=private_reason)
        ),
    )

    breakdown = report["environment_dependency_install_breakdown"]
    assert breakdown["total"] == 1
    assert breakdown["categories"][0] == {
        "category": "pinned_distribution_unavailable",
        "count": 1,
        "fraction_of_dependency_install_failures": 1.0,
    }
    public = create_public_fix_coverage_report(
        corpus=corpus,
        private_root=tmp_path / "run",
        output=tmp_path / "public.json",
        execution_source_revision="a" * 40,
    )
    assert private_reason not in json.dumps(public)
    assert public["items"][0]["environment_install_category"] == ("pinned_distribution_unavailable")


def test_public_report_records_multiple_execution_source_segments(tmp_path: Path) -> None:
    corpus = FixCorpus(
        path=str(tmp_path / "corpus.json"),
        sha256="f" * 64,
        preregistration={},
        selection={},
        exclusions=(),
        instances=(_instance(), _instance("owner-repo-pr-2")),
    )
    run_fix_coverage_study(
        corpus=corpus,
        output_root=tmp_path / "run",
        config=_config(),
        runner=lambda instance, root, timeout: _result(case=_uncertain()),
    )

    public = create_public_fix_coverage_report(
        corpus=corpus,
        private_root=tmp_path / "run",
        output=tmp_path / "public.json",
        execution_source_revision="a" * 40,
        execution_segments=[
            {"first_instance": 1, "last_instance": 1, "source_revision": "a" * 40},
            {
                "first_instance": 2,
                "last_instance": 2,
                "source_revision": "b" * 40,
                "reason": "resume-only correction",
            },
        ],
    )

    assert public["execution_source_revisions"] == ["a" * 40, "b" * 40]
    assert public["execution_segments"][1]["first_instance_id"] == "owner-repo-pr-2"
    assert public["execution_segments"][1]["reason"] == "resume-only correction"

    with pytest.raises(ValueError, match="cover the corpus"):
        create_public_fix_coverage_report(
            corpus=corpus,
            private_root=tmp_path / "run",
            output=tmp_path / "invalid-public.json",
            execution_source_revision="a" * 40,
            execution_segments=[
                {"first_instance": 2, "last_instance": 2, "source_revision": "b" * 40}
            ],
        )


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


def test_prior_corpus_members_are_excluded_before_the_repository_cap(tmp_path: Path) -> None:
    prior = {
        "schema_version": CORPUS_SCHEMA,
        "instances": [{"source_url": "https://github.com/owner/repo/pull/1"}],
    }
    manifest = tmp_path / "prior.json"
    manifest.write_text(json.dumps(prior))
    sources, repositories, provenance = _load_exclusion_manifests(manifest, False)
    candidates = [
        {"number": 1, "html_url": "https://github.com/owner/repo/pull/1"},
        {"number": 2, "html_url": "https://github.com/owner/repo/pull/2"},
    ]

    kept, excluded = _exclude_prior_candidates(candidates, sources)

    assert [item["number"] for item in kept] == [2]
    assert [item["number"] for item in excluded] == [1]
    assert provenance is not None
    assert provenance["instances"] == 1
    assert provenance["rule"] == (
        "exclude matching source_url before applying the per-repository cap"
    )
    # Instance-level freshness collects no repositories, so nothing is skipped wholesale.
    assert repositories == set()


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


def test_subprocess_history_does_not_replay_a_completed_quota_attempt(tmp_path: Path) -> None:
    workers = tmp_path / "workers"
    attempt = workers / "attempt-001"
    attempt.mkdir(parents=True)
    result = _result(case=_quota_exhausted())
    (attempt / "result.json").write_text(
        json.dumps({"schema_version": WORKER_SCHEMA, "result": result.to_dict()})
    )

    reusable, quota_attempts = _worker_attempt_history(workers)

    assert reusable is None, "resume must launch a fresh attempt after quota returns"
    assert quota_attempts == 1, "the non-outcome attempt remains visible in the audit trail"


def test_the_report_records_the_platform_it_measured_on(tmp_path: Path) -> None:
    """Install failures are platform-specific, so a number without a platform is unusable."""
    corpus = _corpus_of(tmp_path, 1)

    def runner(instance: FixInstance, root: Path, timeout_s: float) -> WorkerResult:
        return replace(
            _result(
                case={
                    "verdict": "VERIFIED",
                    "hypotheses": [{"reason": None}],
                    "evidence": {},
                    "proposal_runs": [],
                }
            ),
            instance_id=instance.id,
        )

    report = run_fix_coverage_study(
        corpus=corpus, output_root=tmp_path / "run", config=_config(), runner=runner
    )
    recorded = report["runtime_platform"]

    assert set(recorded) == {
        "host_machine",
        "host_system",
        "sandbox_os",
        "sandbox_architecture",
    }
    assert recorded["host_machine"], "the host architecture is always knowable"


def test_resume_preserves_the_original_runtime_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report regeneration must not silently change the platform provenance."""
    from exhibit_a.studies import fix_coverage

    recorded = {
        "host_machine": "arm64",
        "host_system": "darwin",
        "sandbox_os": "linux",
        "sandbox_architecture": "arm64",
    }
    probes = 0

    def platform_probe() -> dict[str, str]:
        nonlocal probes
        probes += 1
        if probes > 1:
            raise AssertionError("resume probed a new platform")
        return recorded

    monkeypatch.setattr(fix_coverage, "_runtime_platform", platform_probe)
    corpus = _corpus_of(tmp_path, 1)

    def runner(instance: FixInstance, root: Path, timeout: float) -> WorkerResult:
        return replace(_result(case=_uncertain()), instance_id=instance.id)

    first = run_fix_coverage_study(
        corpus=corpus, output_root=tmp_path / "run", config=_config(), runner=runner
    )
    resumed = run_fix_coverage_study(
        corpus=corpus, output_root=tmp_path / "run", config=_config(), runner=runner
    )

    assert first["runtime_platform"] == recorded
    assert resumed["runtime_platform"] == recorded
    assert probes == 1


def test_a_missing_docker_leaves_the_sandbox_platform_unknown_not_wrong(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guessing the sandbox platform would be worse than admitting it is unknown."""
    from exhibit_a.studies import fix_coverage

    def absent(*args: object, **kwargs: object):
        raise OSError("docker not found")

    monkeypatch.setattr(fix_coverage.subprocess, "run", absent)
    recorded = fix_coverage._runtime_platform()

    assert recorded["sandbox_os"] is None
    assert recorded["sandbox_architecture"] is None
    assert recorded["host_machine"]


def test_preflight_never_outranks_an_instance_the_judge_ruled_on() -> None:
    # The preflight stopped ending runs, so a red or unrunnable repository suite is a
    # description of the repository. Once a candidate has been judged, the judge's reason
    # is what happened -- otherwise these instances vanish from judged_denominator and the
    # study under-reports its own reach.
    for suite_passed in (False, None):
        result = _result(
            case=_uncertain(
                existing_suite_passed=suite_passed,
                existing_suite_log="ERROR collecting tests/conftest.py",
                hypotheses=[{"reason": "test imports nothing - it cannot exercise the code"}],
            )
        )
        assert classify_worker_result(result)[0] == "candidate_vacuous"


def test_preflight_log_cannot_relabel_a_judged_instance_as_a_timeout() -> None:
    # The repository's own suite log is not evidence about what stopped this instance.
    result = _result(
        case=_uncertain(
            existing_suite_passed=None,
            existing_suite_log="tests/test_slow.py::test_x Timeout: the test timed out",
            hypotheses=[{"reason": "does not fail on the target"}],
        )
    )

    assert classify_worker_result(result)[0] == "candidate_did_not_fail_on_buggy"


def test_a_missing_system_library_is_named_rather_than_pooled_with_harness_failures() -> None:
    # python:3.12-slim omits shared objects that common wheels link against. That is our
    # ceiling and we can fix it, so it must not disappear into the generic bucket that
    # also holds genuinely broken candidates.
    result = _result(
        case=_uncertain(
            hypotheses=[
                {"reason": "target failed for an environmental/harness reason (ImportError)"}
            ],
            evidence={
                "fail_log": "ImportError: libGL.so.1: cannot open shared object file",
                "pass_log": "",
                "runs": [],
            },
        )
    )

    category, _ = classify_worker_result(result)

    assert category == "candidate_system_library_missing"
    # The judge still ruled on a candidate, so the instance stays inside the reach metric.
    assert category in _JUDGED_FAILURE_CATEGORIES


def test_a_harness_failure_without_a_loader_error_keeps_the_generic_category() -> None:
    result = _result(
        case=_uncertain(
            hypotheses=[{"reason": "target failed for an environmental/harness reason (fixture )"}],
            evidence={"fail_log": "fixture 'db' not found", "pass_log": "", "runs": []},
        )
    )

    assert classify_worker_result(result)[0] == "candidate_infrastructure_failure"


def _probe_config(**updates: object) -> RunConfig:
    base = {
        "requested_model": "none (reach probe)",
        "provider_config": None,
        "instance_timeout_s": 60.0,
        "total_ceiling_s": 600.0,
        "execution_timeout_s": 30,
        "reruns": 1,
        "max_refine": 0,
        "probe_only": True,
    }
    base.update(updates)
    return RunConfig(**base)


def test_a_reach_probe_refuses_to_carry_a_provider(tmp_path: Path) -> None:
    # The point of a probe is that it cannot spend a model call. Accepting a provider
    # configuration and then quietly ignoring it would make that promise unverifiable.
    with pytest.raises(ValueError, match="runs no provider"):
        _validate_config(_probe_config(provider_config=str(tmp_path / "provider.json")))


def test_a_reach_probe_withholds_verified_figures_rather_than_reporting_zero(
    tmp_path: Path,
) -> None:
    # A stub proposer cannot clear the gate, so a zero here would read as a measured
    # coverage result and would be a lie. Reach is what a probe measures, so reach is
    # what it reports.
    corpus = _corpus_of(tmp_path, 2)
    config = _probe_config()
    # What a probe actually observes: a stub test that imports nothing, rejected as
    # vacuous. The rejection is the proof that the judge got a turn.
    vacuous = _silent("test imports nothing - it cannot exercise the code under test")

    def runner(instance: FixInstance, root: Path, timeout_s: float) -> WorkerResult:
        return replace(_result(case=vacuous), instance_id=instance.id)

    report = run_fix_coverage_study(
        corpus=corpus, output_root=tmp_path / "run", config=config, runner=runner
    )

    assert report["probe_only"] is True
    headline = report["headline"]
    for withheld in (
        "verified",
        "verified_fraction",
        "verified_wilson_95",
        "partial",
        "partial_fraction",
        "verified_fraction_of_attempted",
        "verified_fraction_of_judged",
        "verified_judged_wilson_95",
    ):
        assert headline[withheld] is None, withheld
    # The whole reason to run one: both instances reached the deterministic judge.
    assert headline["judged_denominator"] == 2
    assert headline["reached_judge_fraction"] == 1.0

    # The flag has to survive into the artifact people actually read. Without it a
    # published probe is a report full of nulls with no way to tell it apart from a run
    # whose provider fell over.
    public = create_public_fix_coverage_report(
        corpus=corpus,
        private_root=tmp_path / "run",
        output=tmp_path / "public.json",
        execution_source_revision="a" * 40,
    )
    assert public["probe_only"] is True
    assert public["headline"]["verified"] is None


def test_repository_names_outside_the_id_alphabet_still_select() -> None:
    """A period in a repository name cost pilot v7 a selection run.

    `plotly/plotly.py` produced `plotly-plotly.py-pr-5517`, which the study runner
    rejected before instance one -- after selection had already cloned and validated
    every repository. Folding is generic rather than a list of known offenders, so the
    next name shape nobody anticipated cannot cost another run.
    """
    assert fix_corpus._slug("plotly/plotly.py") == "plotly-plotly-py"
    for full_name in ("plotly/plotly.py", "a_b/c.d.e", "--weird--/..name..", "HKUDS/LightRAG"):
        assert _ID.fullmatch(f"{fix_corpus._slug(full_name)}-pr-1"), full_name
    # Names that were already valid keep the identifiers they had.
    assert fix_corpus._slug("HKUDS/LightRAG") == "hkuds-lightrag"
    assert fix_corpus._slug("virattt/ai-hedge-fund") == "virattt-ai-hedge-fund"


def test_repository_level_freshness_reads_every_prior_corpus(tmp_path: Path) -> None:
    """Instance-level freshness let v7 reuse 17 of v6's 21 repositories.

    That is fine for measuring the product and useless for asking whether an engine
    change generalizes, because the repositories were the ones the change was derived
    from. A corpus that wants an answer to the second question has to drop them whole.
    """
    manifests = []
    for index, (owner, pull) in enumerate((("oraios/serena", 1), ("HKUDS/LightRAG", 2))):
        path = tmp_path / f"prior-{index}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": CORPUS_SCHEMA,
                    "instances": [
                        {
                            "source_url": f"https://github.com/{owner}/pull/{pull}",
                            "repository": f"https://github.com/{owner}.git",
                        }
                    ],
                }
            )
        )
        manifests.append(path)

    sources, repositories, provenance = _load_exclusion_manifests(manifests, True)

    assert len(sources) == 2
    # Case-folded and stripped of the .git suffix, so it compares against GitHub's full_name.
    assert repositories == {"oraios/serena", "hkuds/lightrag"}
    assert provenance["excludes_repositories"] is True
    assert provenance["instances"] == 2
    assert provenance["repositories"] == 2
    assert len(provenance["manifests"]) == 2
    assert (
        provenance["rule"] == "exclude every repository named by a prior corpus before eligibility"
    )
