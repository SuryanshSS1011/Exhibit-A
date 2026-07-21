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

from .engine import EngineConfig, EvidenceEngine
from .executor.base import RepoState
from .executor.local_exec import LocalExecutor
from .hypothesis.generator import Candidate, Claim, CodexGenerator, Feedback, StubGenerator
from .intake.git_bisect import bisect_reproduction
from .intake.git_checkout import checkout_context, checkout_pair, checkout_triplet
from .models.case import Case, Mode, Verdict
from .store.json_store import JsonCaseStore


def _build_engine(
    use_docker: bool,
    offline: bool,
    allow_reproduced: bool = False,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> EvidenceEngine:
    if use_docker:
        from .executor.docker_exec import DockerExecutor

        executor = DockerExecutor()
    else:
        executor = LocalExecutor()
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
        claim = Claim(
            text=claim_text,
            repo_path=str(Path(args.repo).resolve()),
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
        case = engine.investigate(
            claim,
            mode=Mode.DETECTIVE,
            target=target,
            base=base,
            control=control,
        )

    store = JsonCaseStore(args.out)
    path = store.save(case)

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
