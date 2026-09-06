"""Choose the interpreter a checkout declares it needs, not the one we happen to ship.

Every environment used to be built on ``python:3.12-slim`` regardless of what the project
required. For a project pinned below 3.12 that is wrong twice over. Its lockfile holds
wheels tagged for the interpreter it resolved against, so pip finds nothing it can use and
falls back to building every binary package from source -- which reads as a missing
toolchain and is really a missing interpreter. And if the install had somehow succeeded,
the tests would have run on a Python the project says it does not support, so nothing
observed there would be evidence about the project.
"""

from __future__ import annotations

import re
from pathlib import Path

# Interpreters this sandbox is willing to build on. Adding one is a deliberate act: it
# widens what the engine claims to support and every image is rebuilt against it.
SUPPORTED = ((3, 11), (3, 12), (3, 13))
DEFAULT = (3, 12)

_REQUIRES = re.compile(r'^\s*requires-python\s*=\s*"([^"]+)"', re.MULTILINE)
_CLAUSE = re.compile(r"^(==|!=|>=|<=|~=|>|<)\s*([0-9][0-9.]*)(\.\*)?$")


def declared_python(root: Path) -> str | None:
    """Return the ``requires-python`` specifier a checkout declares, or None.

    ``uv.lock`` is read first. It states the range the resolution was actually produced
    for, which is what the wheel tags inside it were built against, and that is the thing
    the install has to match. ``pyproject.toml`` is the fallback for projects with no lock.
    """
    for name in ("uv.lock", "pyproject.toml"):
        candidate = root / name
        if not candidate.is_file():
            continue
        try:
            match = _REQUIRES.search(candidate.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if match:
            return match.group(1)
    return None


def select_python(specifier: str | None) -> tuple[int, int] | None:
    """Pick a supported interpreter satisfying ``specifier``.

    The default is kept whenever it qualifies, so a project that never cared which
    interpreter it got is built exactly as before and nothing that works today moves.
    Returns None when the sandbox ships nothing the project accepts, which is a refusal
    to test a project on an interpreter it rejects rather than a silent substitution.
    """
    if not specifier or not specifier.strip():
        return DEFAULT
    satisfying = [version for version in SUPPORTED if _satisfies(version, specifier)]
    if not satisfying:
        return None
    if DEFAULT in satisfying:
        return DEFAULT
    # Nearest to the default, preferring the newer of two equally distant options.
    return min(satisfying, key=lambda version: (abs(version[1] - DEFAULT[1]), -version[1]))


def image_for(version: tuple[int, int]) -> str:
    return f"python:{version[0]}.{version[1]}-slim"


def _satisfies(version: tuple[int, int], specifier: str) -> bool:
    for raw in specifier.split(","):
        clause = raw.strip()
        if not clause:
            continue
        match = _CLAUSE.match(clause)
        if match is None:
            # An unparseable clause is not evidence of anything. Treating it as satisfied
            # keeps the default in play rather than refusing a project over our own gap.
            continue
        operator, literal, wildcard = match.groups()
        try:
            bound = tuple(int(part) for part in literal.split(".") if part != "")
        except ValueError:
            continue
        if not bound:
            continue
        if not _compare(version, operator, bound, bool(wildcard)):
            return False
    return True


def _compare(
    version: tuple[int, int], operator: str, bound: tuple[int, ...], wildcard: bool
) -> bool:
    if operator in {"==", "!="} and (wildcard or len(bound) <= 2):
        # `==3.11.*` and a bare `==3.11` both name a release series, and a patch-level
        # bound cannot exclude a series we identify only as major.minor.
        equal = version[: len(bound)] == bound
        return equal if operator == "==" else not equal
    left = version + (0,) * (len(bound) - len(version))
    right = bound + (0,) * (len(version) - len(bound))
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    if operator == ">=":
        return left >= right
    if operator == "<=":
        return left <= right
    if operator == ">":
        return left > right
    if operator == "<":
        return left < right
    if operator == "~=":
        # Compatible release: at least this version, and within its series.
        series = bound[:-1] if len(bound) > 1 else bound
        return left >= right and version[: len(series)] == series[: len(version)]
    return True
