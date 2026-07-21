"""`exhibit-a` CLI — the developer-facing intake surface (plan §2, surface 3).

    exhibit-a repro <repo_path> --trace trace.txt
    exhibit-a repro <repo_path> --claim "list_users drops the last row"

For the MVP this wires the StubGenerator + LocalExecutor so the loop runs offline.
Swap in a Codex-backed generator and the DockerExecutor for real reproduction.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import EngineConfig, EvidenceEngine
from .executor.local_exec import LocalExecutor
from .hypothesis.generator import Claim, StubGenerator
from .models.case import Mode
from .store.json_store import JsonCaseStore


def _build_engine(use_docker: bool) -> EvidenceEngine:
    if use_docker:
        from .executor.docker_exec import DockerExecutor

        executor = DockerExecutor()
    else:
        executor = LocalExecutor()
    return EvidenceEngine(StubGenerator(), executor, EngineConfig())


def cmd_repro(args: argparse.Namespace) -> int:
    claim_text = args.claim or ""
    if args.trace:
        claim_text = Path(args.trace).read_text()
    if not claim_text.strip():
        print("error: provide --claim or --trace", file=sys.stderr)
        return 2

    claim = Claim(
        text=claim_text,
        repo_path=str(Path(args.repo).resolve()),
        expected_signature=args.expect,
    )
    engine = _build_engine(use_docker=args.docker)
    case = engine.investigate(claim, mode=Mode.DETECTIVE)

    store = JsonCaseStore(args.out)
    path = store.save(case)

    print(f"\n=== VERDICT: {case.verdict.value} ===")
    if case.silence_reason:
        print(f"silence_reason: {case.silence_reason}")
    print(f"case file: {path}")
    if args.json:
        print(json.dumps(case.to_dict(), indent=2, default=str))
    return 0 if case.is_proven() else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="exhibit-a", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("repro", help="reproduce a bug into a verified failing test")
    p.add_argument("repo", help="path to a local repo checkout")
    p.add_argument("--claim", help="a bug description / concern")
    p.add_argument("--trace", help="path to a file containing a stack trace")
    p.add_argument("--expect", help="expected failure signature (e.g. 'KeyError')")
    p.add_argument("--docker", action="store_true", help="use the Docker executor")
    p.add_argument("--out", default=".exhibit-a/cases", help="case output dir")
    p.add_argument("--json", action="store_true", help="print the full Case JSON")
    p.set_defaults(func=cmd_repro)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
