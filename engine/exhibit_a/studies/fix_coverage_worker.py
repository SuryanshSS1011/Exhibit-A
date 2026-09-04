"""Isolated worker for one fix-coverage corpus instance."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from ..engine import EngineConfig, EvidenceEngine
from ..executor.docker_exec import DockerExecutor
from ..executor.instrumented import RecordingExecutor
from ..hypothesis.generator import Claim, CodexGenerator
from ..intake.git_checkout import checkout_pair
from ..models.case import Mode
from ..providers import ProviderRole, load_provider_config
from ..store.suite_gap import ENGINE_VERSION
from .fix_coverage import (
    WORKER_SCHEMA,
    FixInstance,
    RunConfig,
    WorkerResult,
    _atomic_json,
)


def run_request(request: dict) -> WorkerResult:
    """Execute exactly one ordinary Detective investigation in the Docker sandbox."""
    if request.get("schema_version") != WORKER_SCHEMA:
        raise ValueError(f"worker request schema must be {WORKER_SCHEMA!r}")
    raw_instance = request.get("instance")
    raw_config = request.get("config")
    if not isinstance(raw_instance, dict) or not isinstance(raw_config, dict):
        raise TypeError("worker request requires instance and config objects")
    instance = FixInstance(**raw_instance)
    config = RunConfig(**raw_config)
    attempt = int(request.get("attempt", 1))
    prior = int(request.get("prior_incomplete_attempts", 0))
    environment_root = Path(str(request["environment_root"])).resolve()

    started_at = datetime.now(timezone.utc).isoformat()
    start = time.monotonic()
    executor = RecordingExecutor(DockerExecutor(), environment_root)
    try:
        if config.provider_config is None:
            generator = CodexGenerator(model=config.requested_model)
        else:
            provider = load_provider_config(config.provider_config).provider_for(
                ProviderRole.PROPOSER
            )
            generator = CodexGenerator(
                provider=provider,
                model=getattr(provider, "model", config.requested_model),
            )
        engine = EvidenceEngine(
            generator,
            executor,
            EngineConfig(
                reruns=config.reruns,
                max_refine=config.max_refine,
                timeout_s=config.execution_timeout_s,
                check_existing_suite=config.check_existing_suite,
                minimize_proven=config.minimize_verified,
                score_evidence_strength=config.score_evidence_strength,
            ),
        )
        with checkout_pair(
            instance.repository,
            instance.buggy_sha,
            instance.fix_sha,
        ) as (target, base):
            claim = Claim(instance.claim, target.path)
            case = engine.investigate(
                claim,
                mode=Mode.DETECTIVE,
                target=target,
                base=base,
                repo_source=instance.repository,
            )
        return WorkerResult(
            instance_id=instance.id,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            wall_time_s=round(time.monotonic() - start, 6),
            case=case.to_dict(),
            error_type=None,
            error=None,
            attempt=attempt,
            prior_incomplete_attempts=prior,
        )
    except Exception as exc:
        return WorkerResult(
            instance_id=instance.id,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            wall_time_s=round(time.monotonic() - start, 6),
            case=None,
            error_type=type(exc).__name__,
            error=str(exc)[-4000:],
            attempt=attempt,
            prior_incomplete_attempts=prior,
        )
    finally:
        executor.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    output = Path(args.out).resolve()
    try:
        request = json.loads(Path(args.request).read_text())
        if not isinstance(request, dict):
            raise TypeError("worker request must contain a JSON object")
        result = run_request(request)
    except Exception as exc:
        now = datetime.now(timezone.utc).isoformat()
        result = WorkerResult(
            instance_id="invalid-request",
            started_at=now,
            finished_at=now,
            wall_time_s=0.0,
            case=None,
            error_type=type(exc).__name__,
            error=str(exc)[-4000:],
        )
    _atomic_json(
        output,
        {
            "schema_version": WORKER_SCHEMA,
            "engine_version": ENGINE_VERSION,
            "result": result.to_dict(),
        },
    )
    return 0 if result.error_type is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
