"""False-conviction audit over behavior-preserving refactor pairs."""

from __future__ import annotations

import json
import math
import re
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..engine import EvidenceEngine
from ..executor.base import ExecSpec, RepoState
from ..hypothesis.generator import Claim
from ..models.case import Mode, Verdict
from ..store.suite_gap import ENGINE_VERSION

SCHEMA_VERSION = "self-audit/v1"
_CORPUS_SCHEMA = "refactor-corpus/v1"
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class RefactorPair:
    id: str
    category: str
    claim: str
    expected_signature: str | None
    base: RepoState
    target: RepoState
    contract_path: str
    contract_code: str


@dataclass(frozen=True)
class RateEstimate:
    evaluated: int
    false_convictions: int
    rate: float | None
    lower_95: float | None
    upper_95: float | None


@dataclass(frozen=True)
class AuditItem:
    id: str
    category: str
    variant: str
    corpus_valid: bool
    corpus_reason: str | None
    case: dict | None
    verdict: str | None
    false_conviction: bool
    error: str | None


@dataclass(frozen=True)
class SelfAuditReport:
    schema_version: str
    engine_version: str
    id: str
    created_at: str
    corpus: str
    overall: RateEstimate
    by_category: dict[str, RateEstimate]
    items: tuple[AuditItem, ...]

    def to_dict(self) -> dict:
        return asdict(self)


AuditEngineFactory = Callable[[RefactorPair, int], tuple[EvidenceEngine, str]]


def load_refactor_corpus(root: str | Path) -> tuple[RefactorPair, ...]:
    """Load a path-contained, versioned behavior-preserving refactor corpus."""
    corpus_root = Path(root).resolve()
    manifest_path = corpus_root / "manifest.json"
    payload = json.loads(manifest_path.read_text())
    if payload.get("schema_version") != _CORPUS_SCHEMA or not isinstance(
        payload.get("cases"), list
    ):
        raise ValueError("invalid refactor corpus manifest")
    pairs: list[RefactorPair] = []
    seen: set[str] = set()
    for raw in payload["cases"]:
        if not isinstance(raw, dict):
            raise ValueError("refactor corpus entry must be an object")
        pair_id = str(raw.get("id", ""))
        if not _ID.fullmatch(pair_id) or pair_id in seen:
            raise ValueError(f"invalid or duplicate refactor id: {pair_id!r}")
        seen.add(pair_id)
        base = _contained(corpus_root, raw.get("base"), directory=True)
        target = _contained(corpus_root, raw.get("target"), directory=True)
        contract = _contained(corpus_root, raw.get("contract"), directory=False)
        contract_code = contract.read_text()
        pairs.append(
            RefactorPair(
                id=pair_id,
                category=str(raw.get("category", "unspecified")),
                claim=str(raw.get("claim", "")),
                expected_signature=(
                    str(raw["expected_signature"]) if raw.get("expected_signature") else None
                ),
                base=RepoState(str(base), "base", source=f"refactor-corpus:{pair_id}:base"),
                target=RepoState(str(target), "target", source=f"refactor-corpus:{pair_id}:target"),
                contract_path="test_refactor_contract.py",
                contract_code=contract_code,
            )
        )
    return tuple(pairs)


def run_self_audit(
    *,
    corpus_root: str | Path,
    engine_factory: AuditEngineFactory,
    timeout_s: int = 120,
) -> SelfAuditReport:
    """Validate each innocent pair, then measure whether Prosecutor falsely speaks."""
    pairs = load_refactor_corpus(corpus_root)
    items: list[AuditItem] = []
    for index, pair in enumerate(pairs):
        engine: EvidenceEngine | None = None
        variant = f"run-{index}"
        try:
            engine, variant = engine_factory(pair, index)
            valid, reason = _validate_pair(engine, pair, timeout_s)
            if not valid:
                items.append(
                    AuditItem(
                        pair.id,
                        pair.category,
                        variant,
                        False,
                        reason,
                        None,
                        None,
                        False,
                        None,
                    )
                )
                continue
            case = engine.investigate(
                Claim(pair.claim, pair.target.path, pair.expected_signature),
                mode=Mode.PROSECUTOR,
                target=pair.target,
                base=pair.base,
                repo_source=pair.target.source,
            )
            convicted = case.verdict is Verdict.PROVEN
            items.append(
                AuditItem(
                    pair.id,
                    pair.category,
                    variant,
                    True,
                    None,
                    case.to_dict(),
                    case.verdict.value,
                    convicted,
                    None,
                )
            )
        except Exception as exc:
            items.append(
                AuditItem(
                    pair.id,
                    pair.category,
                    variant,
                    False,
                    None,
                    None,
                    None,
                    False,
                    f"{type(exc).__name__}: {exc}",
                )
            )
        finally:
            if engine is not None:
                engine.executor.close()

    valid_items = [item for item in items if item.corpus_valid and item.error is None]
    grouped: dict[str, list[AuditItem]] = defaultdict(list)
    for item in valid_items:
        grouped[item.category].append(item)
    return SelfAuditReport(
        schema_version=SCHEMA_VERSION,
        engine_version=ENGINE_VERSION,
        id=uuid.uuid4().hex[:12],
        created_at=datetime.now(timezone.utc).isoformat(),
        corpus=str(Path(corpus_root).resolve()),
        overall=_rate(valid_items),
        by_category={category: _rate(group) for category, group in sorted(grouped.items())},
        items=tuple(items),
    )


def save_self_audit_report(report: SelfAuditReport, root: str | Path) -> Path:
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{report.id}.json"
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return path


def wilson_interval(false_convictions: int, evaluated: int) -> tuple[float, float] | None:
    """Two-sided Wilson 95% interval for the false-conviction proportion."""
    if evaluated < 1 or false_convictions < 0 or false_convictions > evaluated:
        return None
    z = 1.959963984540054
    proportion = false_convictions / evaluated
    denominator = 1 + z * z / evaluated
    center = (proportion + z * z / (2 * evaluated)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1 - proportion) / evaluated + z * z / (4 * evaluated * evaluated))
        / denominator
    )
    return (
        round(max(0.0, center - margin), 12),
        round(min(1.0, center + margin), 12),
    )


def _validate_pair(
    engine: EvidenceEngine, pair: RefactorPair, timeout_s: int
) -> tuple[bool, str | None]:
    spec = ExecSpec(
        test_path=pair.contract_path,
        test_code=pair.contract_code,
        command=f"python3 -m pytest -x -q {pair.contract_path}",
        timeout_s=timeout_s,
    )
    target = engine.executor.run(pair.target, spec)
    base = engine.executor.run(pair.base, spec)
    if target.passed and base.passed:
        return True, None
    return (
        False,
        "behavior contract failed on "
        + ", ".join(
            state for state, outcome in (("base", base), ("target", target)) if not outcome.passed
        ),
    )


def _rate(items: list[AuditItem]) -> RateEstimate:
    false_convictions = sum(item.false_conviction for item in items)
    interval = wilson_interval(false_convictions, len(items))
    return RateEstimate(
        evaluated=len(items),
        false_convictions=false_convictions,
        rate=false_convictions / len(items) if items else None,
        lower_95=interval[0] if interval else None,
        upper_95=interval[1] if interval else None,
    )


def _contained(root: Path, raw: object, *, directory: bool) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ValueError("refactor corpus path is missing")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe refactor corpus path: {raw!r}")
    try:
        resolved = root.joinpath(relative).resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"refactor corpus path does not exist: {raw!r}") from exc
    if not resolved.is_relative_to(root) or resolved.is_dir() is not directory:
        raise ValueError(f"invalid refactor corpus path: {raw!r}")
    return resolved
