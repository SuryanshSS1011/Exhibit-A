"""Deterministic traceback-to-diff matching for Prosecutor evidence."""

from __future__ import annotations

import difflib
import re
from pathlib import Path

from ..executor.base import ExecOutcome

ChangedLines = dict[str, set[int]]

_PYTHON_FRAME = re.compile(r'^\s*File "([^"]+)", line (\d+)', re.M)
_PYTEST_LOCATION = re.compile(r"^(?P<path>[^\s:][^:]*\.py):(?P<line>\d+)(?::|\s)", re.M)
_MAX_SOURCE_BYTES = 2_000_000


def changed_line_map(base_path: str, target_path: str) -> ChangedLines:
    """Return target-side changed Python lines between two checkout trees."""
    base = Path(base_path).resolve()
    target = Path(target_path).resolve()
    base_files = _python_files(base)
    target_files = _python_files(target)
    changed: ChangedLines = {}

    for relative in sorted(base_files.keys() | target_files.keys()):
        before = _read_lines(base_files.get(relative))
        after = _read_lines(target_files.get(relative))
        if before is None or after is None:
            continue
        lines: set[int] = set()
        for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(
            None, before, after, autojunk=False
        ).get_opcodes():
            if tag == "equal":
                continue
            if j1 != j2:
                lines.update(range(j1 + 1, j2 + 1))
            elif after:
                lines.add(min(j1 + 1, len(after)))
        if lines:
            changed[relative] = lines
    return changed


def traceback_touches_changed_lines(outcome: ExecOutcome, changed_lines: ChangedLines) -> bool:
    """Return whether a traceback is on, or downstream of, a changed frame."""
    frames = [(path, int(line)) for path, line in _PYTHON_FRAME.findall(outcome.log)]
    frames.extend(
        (match.group("path"), int(match.group("line")))
        for match in _PYTEST_LOCATION.finditer(outcome.log)
    )
    return any(
        line in changed
        for frame_path, line in frames
        for relative, changed in changed_lines.items()
        if _same_repo_path(frame_path, relative)
    )


def _python_files(root: Path) -> dict[str, Path]:
    if not root.is_dir():
        return {}
    files: dict[str, Path] = {}
    for path in root.rglob("*.py"):
        if ".git" in path.parts or not path.is_file():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            continue
        files[resolved.relative_to(root).as_posix()] = resolved
    return files


def _read_lines(path: Path | None) -> list[str] | None:
    if path is None:
        return []
    try:
        if path.stat().st_size > _MAX_SOURCE_BYTES:
            return None
        return path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return None


def _same_repo_path(frame_path: str, relative: str) -> bool:
    normalized = frame_path.replace("\\", "/").removeprefix("./")
    return normalized == relative or normalized.endswith(f"/{relative}")
