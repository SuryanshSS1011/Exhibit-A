"""A dead-simple JSON-file Case store for the hackathon.

Postgres + object storage is the v1 plan (§2 "Storage"); for the MVP a directory
of JSON files is enough to persist Case Files and back the Silence Log + dataset
browser. The web layer reads these directly.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models.case import Case


class JsonCaseStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, case_id: str) -> Path:
        return self.root / f"{case_id}.json"

    def save(self, case: Case) -> Path:
        p = self._path(case.id)
        p.write_text(json.dumps(case.to_dict(), indent=2, default=str))
        return p

    def load(self, case_id: str) -> dict:
        return json.loads(self._path(case_id).read_text())

    def list_ids(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("*.json"))

    def all(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted(self.root.glob("*.json"))]

    def silence_log(self) -> list[dict]:
        """Every case that stayed silent — the negative-results asset (plan §5)."""
        return [c for c in self.all() if c.get("verdict") == "INSUFFICIENT_EVIDENCE"]
