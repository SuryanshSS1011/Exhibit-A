"""Private, versioned longitudinal research records."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..models.case import Case, Verdict
from .suite_gap import ENGINE_VERSION

RESEARCH_SCHEMA = "exhibit-a-research/v1"
_RECORD_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ResearchStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def record_flaky(self, case: Case, *, model: str) -> Path | None:
        reasons = [hyp.reason or "" for hyp in case.hypotheses]
        if not any(reason.startswith("flaky on target") for reason in reasons):
            return None
        return self._write(
            "flaky",
            case.id,
            {
                **self._version(model, case.created_at),
                "case_id": case.id,
                "repo": case.repo,
                "base_commit": case.base_commit,
                "target_commit": case.target_commit,
                "environment_ref": case.environment_ref,
                "claim_text": case.claim_text,
                "test_file": case.to_dict().get("test_file"),
                "run_command": case.run_command,
                "runs": case.to_dict()["evidence"]["runs"],
                "rejection_reasons": reasons,
            },
        )

    def register_observatory(
        self, case: Case, *, model: str, interval_days: int = 7
    ) -> Path | None:
        if case.verdict is not Verdict.PROVEN or case.test_file is None:
            return None
        existing = self.root / "observatory" / f"{case.id}.json"
        if existing.is_file():
            return existing
        next_run = datetime.now(timezone.utc) + timedelta(days=interval_days)
        return self._write(
            "observatory",
            case.id,
            {
                **self._version(model, case.created_at),
                "case_id": case.id,
                "repo": case.repo,
                "test_file": case.to_dict()["test_file"],
                "run_command": case.run_command,
                "expected_signature": case.evidence.fail_signature,
                "interval_days": interval_days,
                "next_run_at": next_run.isoformat(),
                "history": [],
            },
        )

    def record_observation(
        self,
        case_id: str,
        *,
        upstream_sha: str,
        status: str,
        runs: list[dict[str, Any]],
    ) -> Path:
        if not _RECORD_ID.fullmatch(case_id):
            raise ValueError("invalid observatory case id")
        if status not in {"healthy", "re_regressed", "flaky", "inconclusive"}:
            raise ValueError("invalid observatory status")
        path = self.root / "observatory" / f"{case_id}.json"
        if not path.is_file():
            raise ValueError(f"observatory case is not registered: {case_id}")
        record = json.loads(path.read_text())
        observed_at = datetime.now(timezone.utc)
        record["history"].append(
            {
                "observed_at": observed_at.isoformat(),
                "upstream_sha": upstream_sha,
                "status": status,
                "runs": runs,
            }
        )
        record["next_run_at"] = (
            observed_at + timedelta(days=int(record["interval_days"]))
        ).isoformat()
        path.write_text(json.dumps(record, indent=2, sort_keys=True))
        return path

    def _write(self, kind: str, key: str, payload: dict[str, Any]) -> Path:
        if not _RECORD_ID.fullmatch(key):
            raise ValueError("invalid research record id")
        directory = self.root / kind
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{key}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return path

    @staticmethod
    def _version(model: str, created_at: str) -> dict[str, str]:
        return {
            "schema_version": RESEARCH_SCHEMA,
            "engine_version": ENGINE_VERSION,
            "model_version": model,
            "recorded_at": created_at,
        }
