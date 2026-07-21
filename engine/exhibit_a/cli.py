"""`exhibit-a` CLI — the developer-facing intake surface (plan §2, surface 3).

    exhibit-a repro <repo_path> --trace trace.txt
    exhibit-a repro <repo_path> --claim "list_users drops the last row"

By default this wires the Codex generator to the local executor. Pass `--offline`
to exercise the deterministic stub without a model, or `--docker` to isolate runs.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .engine import EngineConfig, EvidenceEngine, candidate_policy_reason
from .eef import create_bundle, verify_bundle
from .executor.base import ExecSpec, RepoState
from .executor.instrumented import RecordingExecutor, summarize_environment_attempts
from .executor.local_exec import LocalExecutor
from .hypothesis.generator import Candidate, Claim, CodexGenerator, Feedback, StubGenerator
from .intake.git_bisect import bisect_reproduction
from .intake.git_checkout import checkout_context, checkout_pair, checkout_triplet
from .models.case import Case, Mode, Verdict
from .store.json_store import JsonCaseStore
from .store.research import ResearchStore
from .store.suite_gap import SuiteGapStore
from .studies.bug_identity import run_bug_identity, save_bug_identity_report
from .studies.reproducibility import (
    run_reproducibility_study,
    save_reproducibility_report,
)
from .studies.oracle_gap import run_oracle_gap, save_oracle_gap_report
from .studies.self_audit import run_self_audit, save_self_audit_report
from .verdict.flip_check import extract_signature, signatures_match


def _build_engine(
    use_docker: bool,
    offline: bool,
    allow_reproduced: bool = False,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
    environment_root: str | Path | None = None,
) -> EvidenceEngine:
    if use_docker:
        from .executor.docker_exec import DockerExecutor

        executor = DockerExecutor()
    else:
        executor = LocalExecutor()
    if environment_root is not None:
        executor = RecordingExecutor(executor, environment_root)
    generator = StubGenerator() if offline else CodexGenerator()
    config = EngineConfig(allow_reproduced=allow_reproduced)
    return EvidenceEngine(generator, executor, config, event_sink=event_sink)


def cmd_repro(args: argparse.Namespace) -> int:
    if args.replay:
        if args.repo:
            print("error: a repo cannot be combined with --replay", file=sys.stderr)
            return 2
        try:
            case = _load_replay(Path(args.replay))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"error: cannot replay Case: {exc}", file=sys.stderr)
            return 2
        if args.events:
            _print_event(
                {"event": "phase", "phase": "replay", "message": "Opening sealed Case File"}
            )
            _print_event({"event": "case", "case": case})
        else:
            print(f"\n=== REPLAYED VERDICT: {case['verdict']} ===")
            print(f"case file: {Path(args.replay).resolve()}")
            if args.json:
                print(json.dumps(case, indent=2))
        return 0 if case["verdict"] in {"PROVEN", "REPRODUCED"} else 1

    if not args.repo:
        print("error: provide a repo, or use --replay <case.json>", file=sys.stderr)
        return 2
    claim_text = args.claim or ""
    if args.trace:
        claim_text = Path(args.trace).read_text()
    if not claim_text.strip():
        print("error: provide --claim or --trace", file=sys.stderr)
        return 2

    event_sink = _print_event if args.events else None
    engine = _build_engine(
        use_docker=args.docker,
        offline=args.offline,
        allow_reproduced=args.reproduced,
        event_sink=event_sink,
        environment_root=Path(args.out).parent / "environment-attempts",
    )
    if bool(args.base_sha) != bool(args.fix_sha):
        print("error: --base-sha and --fix-sha must be provided together", file=sys.stderr)
        return 2
    if bool(args.bad_sha) != bool(args.bisect_good_sha):
        print("error: --bad-sha and --bisect-good-sha must be provided together", file=sys.stderr)
        return 2
    if args.bad_sha and (args.base_sha or args.fixed or args.control or args.control_sha):
        print(
            "error: bisect intake cannot be combined with another comparison state", file=sys.stderr
        )
        return 2
    if args.bad_sha and (not args.docker or not args.reproduced):
        print("error: bisect intake requires --docker and --reproduced", file=sys.stderr)
        return 2
    if args.fixed and args.base_sha:
        print("error: --fixed cannot be combined with --base-sha/--fix-sha", file=sys.stderr)
        return 2
    if args.control and args.base_sha:
        print("error: --control cannot be combined with remote SHA intake", file=sys.stderr)
        return 2
    if args.control_sha and not args.base_sha:
        print("error: --control-sha requires --base-sha and --fix-sha", file=sys.stderr)
        return 2

    if args.bad_sha:
        try:
            with checkout_context(args.repo, args.bad_sha, label="target") as target:
                claim = Claim(
                    text=claim_text,
                    repo_path=target.path,
                    expected_signature=args.expect,
                )
                case = engine.investigate(
                    claim,
                    mode=Mode.DETECTIVE,
                    target=target,
                    repo_source=args.repo,
                )
                if case.verdict is Verdict.REPRODUCED:
                    image = engine.executor.prepare(target)
                    if image is None:
                        raise RuntimeError("bisect requires a prepared Docker image")
                    case = _upgrade_with_bisect(
                        case,
                        claim,
                        engine,
                        args.repo,
                        args.bad_sha,
                        args.bisect_good_sha,
                        image,
                        event_sink,
                    )
        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    elif args.base_sha:
        try:
            if event_sink:
                event_sink({"event": "phase", "phase": "checkout", "message": "Cloning commits"})
            checkouts = (
                checkout_triplet(args.repo, args.base_sha, args.fix_sha, args.control_sha)
                if args.control_sha
                else checkout_pair(args.repo, args.base_sha, args.fix_sha)
            )
            with checkouts as states:
                target, base = states[:2]
                control = states[2] if len(states) == 3 else None
                claim = Claim(
                    text=claim_text,
                    repo_path=target.path,
                    expected_signature=args.expect,
                )
                case = engine.investigate(
                    claim,
                    mode=Mode.DETECTIVE,
                    target=target,
                    base=base,
                    control=control,
                    repo_source=args.repo,
                )
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    else:
        repo_path = Path(args.repo).resolve()
        if not repo_path.is_dir():
            print(f"error: repo checkout not found: {repo_path}", file=sys.stderr)
            return 2
        for label, opt in (("--fixed", args.fixed), ("--control", args.control)):
            if opt and not Path(opt).resolve().is_dir():
                print(f"error: {label} checkout not found: {Path(opt).resolve()}", file=sys.stderr)
                return 2
        claim = Claim(
            text=claim_text,
            repo_path=str(repo_path),
            expected_signature=args.expect,
        )
        target = RepoState(path=claim.repo_path, label="target", source=claim.repo_path)
        base = None
        control = None
        if args.fixed:
            fixed_path = str(Path(args.fixed).resolve())
            base = RepoState(path=fixed_path, label="base", source=fixed_path)
        if args.control:
            control_path = str(Path(args.control).resolve())
            control = RepoState(path=control_path, label="control", source=control_path)
        try:
            case = engine.investigate(
                claim,
                mode=Mode.DETECTIVE,
                target=target,
                base=base,
                control=control,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    store = JsonCaseStore(args.out)
    path = store.save(case)
    SuiteGapStore(Path(args.out).parent / "suite-gaps").save(
        case,
        model=str(getattr(engine.generator, "model", type(engine.generator).__name__)),
    )
    research = ResearchStore(Path(args.out).parent / "research")
    model = str(getattr(engine.generator, "model", type(engine.generator).__name__))
    research.record_flaky(case, model=model)
    research.register_observatory(case, model=model)

    if args.events:
        _print_event({"event": "case", "case": case.to_dict()})
        return 0 if case.is_evidence() else 1

    print(f"\n=== VERDICT: {case.verdict.value} ===")
    if case.silence_reason:
        print(f"silence_reason: {case.silence_reason}")
    print(f"case file: {path}")
    if args.json:
        print(json.dumps(case.to_dict(), indent=2, default=str))
    return 0 if case.is_evidence() else 1


def _print_event(event: dict[str, Any]) -> None:
    print(json.dumps(event, default=str), flush=True)


def cmd_bundle(args: argparse.Namespace) -> int:
    try:
        case = json.loads(Path(args.case).read_text())
        key = Path(args.signing_key).read_bytes()
        path = create_bundle(
            case,
            args.out,
            target_source=args.target_source,
            base_source=args.base_source,
            signing_key=key,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: cannot create EEF bundle: {exc}", file=sys.stderr)
        return 2
    print(path)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        result = verify_bundle(
            args.bundle,
            signing_key=Path(args.signing_key).read_bytes(),
            execute=args.execute,
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: EEF verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result.__dict__, indent=2))
    return 0 if result.execution_verified is not False else 1


def cmd_observe(args: argparse.Namespace) -> int:
    try:
        case = json.loads(Path(args.case).read_text())
        test = case.get("test_file")
        if not isinstance(test, dict):
            raise ValueError("observatory Case has no generated test")
        reruns = max(1, int(case.get("evidence", {}).get("reruns", 1)))
        candidate = Candidate(
            hypothesis="observatory replay",
            test_path=str(test.get("path", "")),
            test_code=str(test.get("code", "")),
            run_command=str(case.get("run_command", "")),
            expected_signature=case.get("evidence", {}).get("fail_signature"),
        )
        reason = candidate_policy_reason(candidate)
        if reason:
            raise ValueError(reason)
        from .executor.docker_exec import DockerExecutor

        with checkout_context(args.repo_url, args.upstream_sha, label="upstream") as upstream:
            executor = RecordingExecutor(DockerExecutor(), Path(args.out) / "environment-attempts")
            image = executor.prepare(upstream)
            spec = ExecSpec(
                test_path=candidate.test_path,
                test_code=candidate.test_code,
                command=candidate.run_command,
                image=image,
            )
            outcomes = [executor.run(upstream, spec) for _ in range(reruns)]
        matches = [
            signatures_match(candidate.expected_signature, extract_signature(outcome))
            for outcome in outcomes
        ]
        if all(outcome.passed for outcome in outcomes):
            status = "healthy"
        elif all(not outcome.passed for outcome in outcomes) and all(matches):
            status = "re_regressed"
        elif any(outcome.passed for outcome in outcomes):
            status = "flaky"
        else:
            status = "inconclusive"
        runs = [
            {
                "exit_code": outcome.exit_code,
                "passed": outcome.passed,
                "log": outcome.log,
                "signature": extract_signature(outcome),
                "duration_s": outcome.duration_s,
            }
            for outcome in outcomes
        ]
        path = ResearchStore(args.out).record_observation(
            str(case["id"]),
            upstream_sha=args.upstream_sha,
            status=status,
            runs=runs,
        )
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"error: observatory run failed: {exc}", file=sys.stderr)
        return 2
    print(f"{status}: {path}")
    return 1 if status == "re_regressed" else 0


def cmd_study(args: argparse.Namespace) -> int:
    """Run the private repeated-reproduction convergence study on local states."""
    claim_text = args.claim or ""
    try:
        if args.trace:
            claim_text = Path(args.trace).read_text()
    except OSError as exc:
        print(f"error: cannot read trace: {exc}", file=sys.stderr)
        return 2
    if not claim_text.strip():
        print("error: provide --claim or --trace", file=sys.stderr)
        return 2

    repo_path = Path(args.repo).resolve()
    fixed_path = Path(args.fixed).resolve()
    control_path = Path(args.control).resolve() if args.control else None
    for label, path in (("repo", repo_path), ("--fixed", fixed_path)):
        if not path.is_dir():
            print(f"error: {label} checkout not found: {path}", file=sys.stderr)
            return 2
    if control_path is not None and not control_path.is_dir():
        print(f"error: --control checkout not found: {control_path}", file=sys.stderr)
        return 2

    claim = Claim(str(claim_text), str(repo_path), args.expect)
    target = RepoState(str(repo_path), "target", source=str(repo_path))
    base = RepoState(str(fixed_path), "base", source=str(fixed_path))
    control = (
        RepoState(str(control_path), "control", source=str(control_path))
        if control_path is not None
        else None
    )
    requested_models: list[str | None] = args.models or [None]

    def engine_factory(index: int) -> tuple[EvidenceEngine, str]:
        if args.docker:
            from .executor.docker_exec import DockerExecutor

            executor = DockerExecutor()
        else:
            executor = LocalExecutor()
        executor = RecordingExecutor(executor, Path(args.out).parent / "environment-attempts")
        selected_model = requested_models[index % len(requested_models)]
        generator = StubGenerator() if args.offline else CodexGenerator(model=selected_model)
        variant = "offline-stub" if args.offline else generator.model
        return EvidenceEngine(generator, executor, EngineConfig()), variant

    try:
        report = run_reproducibility_study(
            claim=claim,
            target=target,
            base=base,
            control=control,
            engine_factory=engine_factory,
            runs=args.runs,
        )
        path = save_reproducibility_report(report, args.out)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"error: reproducibility study failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        print(f"study file: {path}")
        print(
            "verdict convergence: "
            f"{_format_metric(report.verdict.convergence)} "
            f"({report.verdict.coverage:.0%} measured)"
        )
        print(
            "root-cause convergence: "
            f"{_format_metric(report.root_cause.convergence)} "
            f"({report.root_cause.coverage:.0%} measured)"
        )
        print(
            "test-semantic convergence: "
            f"{_format_metric(report.test_semantics.convergence)} "
            f"({report.test_semantics.coverage:.0%} measured)"
        )
        print(f"strict convergence: {'yes' if report.converged else 'no'}")
    return 0


def cmd_self_audit(args: argparse.Namespace) -> int:
    """Measure false convictions on a validated behavior-preserving corpus."""

    def engine_factory(_pair, _index):
        engine = _build_engine(
            args.docker,
            args.offline,
            environment_root=Path(args.out).parent / "environment-attempts",
        )
        variant = str(getattr(engine.generator, "model", type(engine.generator).__name__))
        return engine, variant

    try:
        report = run_self_audit(
            corpus_root=args.corpus,
            engine_factory=engine_factory,
        )
        path = save_self_audit_report(report, args.out)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: self-audit failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        estimate = report.overall
        rate = _format_metric(estimate.rate)
        interval = (
            f"{estimate.lower_95:.1%}–{estimate.upper_95:.1%}"
            if estimate.lower_95 is not None and estimate.upper_95 is not None
            else "unavailable"
        )
        print(f"self-audit file: {path}")
        print(
            f"false-conviction rate: {rate} "
            f"({estimate.false_convictions}/{estimate.evaluated}; 95% CI {interval})"
        )
    return 0


def cmd_oracle_gap(args: argparse.Namespace) -> int:
    """Measure weak-oracle exposure in resolved benchmark instances."""
    if args.docker:
        from .executor.docker_exec import DockerExecutor

        executor = DockerExecutor()
    else:
        executor = LocalExecutor()
    executor = RecordingExecutor(executor, Path(args.out).parent / "environment-attempts")
    try:
        report = run_oracle_gap(
            args.manifest,
            executor,
            reruns=args.reruns,
            max_mutants=args.max_mutants,
        )
        path = save_oracle_gap_report(report, args.out)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: oracle-gap probe failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"oracle-gap file: {path}")
        print(
            f"oracle gap: {_format_metric(report.oracle_gap)} "
            f"({report.survived}/{report.eligible} eligible mutants survived; "
            f"{report.evaluated_instances}/{report.instances} baselines valid)"
        )
    return 0


def cmd_environment_summary(args: argparse.Namespace) -> int:
    try:
        summary = summarize_environment_attempts(args.attempts)
        destination = Path(args.out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(summary, indent=2, sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: environment summary failed: {exc}", file=sys.stderr)
        return 2
    print(f"environment summary: {destination}")
    print(f"recorded attempts: {summary['attempts']}")
    return 0


def cmd_dedup(args: argparse.Namespace) -> int:
    if args.docker:
        from .executor.docker_exec import DockerExecutor

        executor = DockerExecutor()
    else:
        executor = LocalExecutor()
    executor = RecordingExecutor(executor, Path(args.out).parent / "environment-attempts")
    try:
        report = run_bug_identity(args.manifest, executor, reruns=args.reruns)
        path = save_bug_identity_report(report, args.out)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: bug-identity dedup failed: {exc}", file=sys.stderr)
        return 2
    print(f"bug-identity file: {path}")
    print(
        f"distinct execution clusters: {len(report.clusters)} "
        f"across {len(report.valid_cases)} revalidated Cases"
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    return 0


def _format_metric(value: float | None) -> str:
    return f"{value:.0%}" if value is not None else "unavailable"


def _load_replay(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("Case JSON must contain an object")
    if not isinstance(payload.get("id"), str) or not payload["id"]:
        raise ValueError("Case JSON is missing a valid id")
    if payload.get("verdict") not in {"PROVEN", "REPRODUCED", "INSUFFICIENT_EVIDENCE"}:
        raise ValueError("Case JSON has an invalid verdict")
    if not isinstance(payload.get("evidence"), dict):
        raise ValueError("Case JSON is missing evidence")
    if not isinstance(payload.get("hypotheses"), list):
        raise ValueError("Case JSON is missing hypotheses")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="exhibit-a", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("repro", help="reproduce a bug into a verified failing test")
    p.add_argument(
        "repo",
        nargs="?",
        help="local repo path, or HTTPS repo URL with two SHA flags",
    )
    p.add_argument("--claim", help="a bug description / concern")
    p.add_argument("--trace", help="path to a file containing a stack trace")
    p.add_argument("--expect", help="expected failure signature (e.g. 'KeyError')")
    p.add_argument(
        "--fixed",
        help="path to a fixed/base checkout; required for a PROVEN fail-to-pass verdict",
    )
    p.add_argument("--base-sha", help="buggy/base commit SHA for remote-repo intake")
    p.add_argument("--fix-sha", help="fixing commit or PR-head SHA for remote-repo intake")
    p.add_argument("--bad-sha", help="known-bad commit for Detective bisect intake")
    p.add_argument(
        "--bisect-good-sha",
        help="known-good ancestor; the culprit parent is rechecked by the flip judge",
    )
    p.add_argument(
        "--control",
        help="older/unrelated local checkout; the candidate must pass there",
    )
    p.add_argument(
        "--control-sha",
        help="older/unrelated remote commit SHA; the candidate must pass there",
    )
    p.add_argument(
        "--reproduced",
        action="store_true",
        help="allow the weaker REPRODUCED verdict (signature-matched, no pass state) "
        "when there is no fixed state to flip against; requires --expect",
    )
    p.add_argument("--docker", action="store_true", help="use the Docker executor")
    p.add_argument(
        "--offline",
        action="store_true",
        help="use the deterministic stub instead of Codex (pipeline diagnostics only)",
    )
    p.add_argument("--out", default=".exhibit-a/cases", help="case output dir")
    p.add_argument("--json", action="store_true", help="print the full Case JSON")
    p.add_argument("--events", action="store_true", help="print progress and final Case as JSONL")
    p.add_argument(
        "--replay",
        metavar="CASE_JSON",
        help="replay a sealed Case JSON without invoking Codex or executing tests",
    )
    p.set_defaults(func=cmd_repro)

    bundle = sub.add_parser("bundle", help="mint a deterministic signed EEF archive")
    bundle.add_argument("case", help="Case JSON to package")
    bundle.add_argument("--target-source", required=True, help="target source snapshot")
    bundle.add_argument("--base-source", help="base source snapshot for a full flip")
    bundle.add_argument(
        "--signing-key", required=True, help="file containing at least 32 key bytes"
    )
    bundle.add_argument("--out", required=True, help="output .eef path")
    bundle.set_defaults(func=cmd_bundle)

    verify = sub.add_parser("verify", help="verify an EEF archive offline")
    verify.add_argument("bundle", help="EEF archive to verify")
    verify.add_argument("--signing-key", required=True, help="publisher verification key file")
    verify.add_argument(
        "--execute",
        action="store_true",
        help="rebuild with network disabled and re-run the deterministic flip check",
    )
    verify.set_defaults(func=cmd_verify)

    observe = sub.add_parser("observe", help="re-run a minted test on a pinned upstream SHA")
    observe.add_argument("case", help="minted Case JSON")
    observe.add_argument("repo_url", help="HTTPS upstream repository URL")
    observe.add_argument("--upstream-sha", required=True, help="pinned current upstream SHA")
    observe.add_argument("--out", default=".exhibit-a/research", help="private research root")
    observe.set_defaults(func=cmd_observe)

    study = sub.add_parser(
        "study",
        help="repeat one reproduction and measure root-cause/test convergence",
    )
    study.add_argument("repo", help="local buggy/target checkout")
    study.add_argument("--fixed", required=True, help="local fixed/base checkout")
    study.add_argument("--control", help="optional unrelated control checkout")
    study.add_argument("--claim", help="bug description / concern")
    study.add_argument("--trace", help="path to a file containing a stack trace")
    study.add_argument("--expect", help="expected failure signature")
    study.add_argument("--runs", type=int, default=5, help="independent samples (2–50)")
    study.add_argument(
        "--model",
        dest="models",
        action="append",
        help="Codex model variant; repeat to cycle models across samples",
    )
    study.add_argument("--docker", action="store_true", help="use the Docker executor")
    study.add_argument(
        "--offline",
        action="store_true",
        help="use the deterministic stub for a no-model study smoke test",
    )
    study.add_argument(
        "--out",
        default=".exhibit-a/research/reproducibility",
        help="private study output directory",
    )
    study.add_argument("--json", action="store_true", help="print the full study JSON")
    study.set_defaults(func=cmd_study)

    audit = sub.add_parser(
        "self-audit",
        help="measure false convictions on behavior-preserving refactors",
    )
    audit.add_argument("corpus", help="versioned refactor corpus directory")
    audit.add_argument("--docker", action="store_true", help="use the Docker executor")
    audit.add_argument(
        "--offline",
        action="store_true",
        help="use the deterministic stub for a no-model audit smoke test",
    )
    audit.add_argument(
        "--out",
        default=".exhibit-a/research/self-audit",
        help="private audit output directory",
    )
    audit.add_argument("--json", action="store_true", help="print the full audit JSON")
    audit.set_defaults(func=cmd_self_audit)

    oracle = sub.add_parser(
        "oracle-gap",
        help="measure mutants surviving official tests on resolved benchmark instances",
    )
    oracle.add_argument("manifest", help="versioned resolved-instance manifest")
    oracle.add_argument("--docker", action="store_true", help="use the Docker executor")
    oracle.add_argument("--reruns", type=int, default=2, help="deterministic runs per mutant")
    oracle.add_argument("--max-mutants", type=int, default=128, help="maximum mutants per instance")
    oracle.add_argument(
        "--out",
        default=".exhibit-a/research/oracle-gap",
        help="private oracle-gap output directory",
    )
    oracle.add_argument("--json", action="store_true", help="print the full report JSON")
    oracle.set_defaults(func=cmd_oracle_gap)

    environments = sub.add_parser(
        "environment-summary",
        help="aggregate private environment setup attempts into empirical recipes",
    )
    environments.add_argument("attempts", help="environment-attempt JSON directory")
    environments.add_argument(
        "--out",
        default=".exhibit-a/research/environment-summary.json",
        help="summary output path",
    )
    environments.set_defaults(func=cmd_environment_summary)

    dedup = sub.add_parser(
        "dedup", help="cluster PROVEN Cases by mutual execution on their fixed states"
    )
    dedup.add_argument("manifest", help="versioned local Case/checkouts manifest")
    dedup.add_argument("--docker", action="store_true", help="use the Docker executor")
    dedup.add_argument("--reruns", type=int, default=2, help="deterministic cross-runs")
    dedup.add_argument(
        "--out",
        default=".exhibit-a/research/bug-identity",
        help="private dedup report directory",
    )
    dedup.add_argument("--json", action="store_true", help="print the full report JSON")
    dedup.set_defaults(func=cmd_dedup)

    args = parser.parse_args(argv)
    return args.func(args)


class _FrozenGenerator:
    def __init__(self, candidate: Candidate):
        self.candidate = candidate

    def propose(self, claim: Claim, max_hypotheses: int = 3) -> list[Candidate]:
        return [self.candidate]

    def refine(self, claim: Claim, feedback: Feedback) -> None:
        return None


def _upgrade_with_bisect(
    reproduced: Case,
    claim: Claim,
    engine: EvidenceEngine,
    repo_url: str,
    bad_sha: str,
    good_sha: str,
    image: str,
    event_sink: Callable[[dict[str, Any]], None] | None,
) -> Case:
    test = reproduced.test_file
    if test is None:
        return reproduced
    if event_sink:
        event_sink({"event": "phase", "phase": "bisect", "message": "Tracing first bad commit"})
    result = bisect_reproduction(
        repo_url,
        bad_sha=bad_sha,
        good_sha=good_sha,
        test_path=test.path,
        test_code=test.code,
        run_command=reproduced.run_command,
        image=image,
        docker_bin=getattr(engine.executor, "docker_bin", "docker"),
    )
    reproduced.culprit_commit = result.culprit
    reproduced.culprit_parent_commit = result.parent
    reproduced.evidence.bisect_log = result.log

    candidate = Candidate(
        hypothesis=reproduced.root_cause_narrative,
        test_path=test.path,
        test_code=test.code,
        run_command=reproduced.run_command,
        expected_signature=reproduced.evidence.fail_signature,
    )
    verifier = EvidenceEngine(
        _FrozenGenerator(candidate),
        engine.executor,
        EngineConfig(
            reruns=engine.config.reruns,
            max_refine=0,
            timeout_s=engine.config.timeout_s,
            run_command=engine.config.run_command,
        ),
        event_sink=event_sink,
    )
    with checkout_pair(repo_url, result.culprit, result.parent) as (culprit, parent):
        upgraded = verifier.investigate(
            claim,
            mode=Mode.DETECTIVE,
            target=culprit,
            base=parent,
            repo_source=repo_url,
        )
    if upgraded.verdict is not Verdict.PROVEN:
        return reproduced
    upgraded.id = reproduced.id
    upgraded.culprit_commit = result.culprit
    upgraded.culprit_parent_commit = result.parent
    upgraded.evidence.bisect_log = result.log
    return upgraded


if __name__ == "__main__":
    raise SystemExit(main())
