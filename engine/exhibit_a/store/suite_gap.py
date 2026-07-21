"""Private, versioned register of existing-suite routing outcomes."""

from __future__ import annotations

import json
from pathlib import Path

from ..models.case import Case

ENGINE_VERSION = "0.0.1"
SCHEMA_VERSION = "suite-gap/v1"


class SuiteGapStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, case: Case, *, model: str) -> Path | None:
        if case.suite_gap is None:
            return None
        record = {
            "schema_version": SCHEMA_VERSION,
            "engine_version": ENGINE_VERSION,
            "model_version": model,
            "recorded_at": case.created_at,
            "case_id": case.id,
            "repo": case.repo,
            "target_commit": case.target_commit,
            "claim_text": case.claim_text,
            "existing_suite_caught": not case.suite_gap,
            "additive_suite_gap": case.suite_gap,
            "suite_log": case.existing_suite_log,
        }
        path = self.root / f"{case.id}.json"
        path.write_text(json.dumps(record, indent=2, sort_keys=True))
        return path
