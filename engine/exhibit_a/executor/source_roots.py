"""Work out which directories a checkout needs on ``sys.path`` to import itself.

A run's working directory is the checkout root, so a flat-layout project imports
without help. A ``src/`` layout does not, and neither does a project whose package
sits under ``backend/`` or ``app/``. Without those directories on the path a
generated test raises ``ModuleNotFoundError`` before it can exercise anything, which
looks exactly like a broken candidate and is not one.

This reads directory structure only. It never parses repository configuration and
never imports repository code, so a hostile checkout cannot steer it beyond choosing
which of its own directories get added.
"""

from __future__ import annotations

import os
from pathlib import Path

# Directories that hold tests, docs, or build output rather than the project's own
# importable packages. Putting these on the path shadows real dependencies.
_NON_SOURCE = frozenset(
    {
        "build",
        "dist",
        "doc",
        "docs",
        "example",
        "examples",
        "node_modules",
        "sample",
        "samples",
        "script",
        "scripts",
        "site",
        "site-packages",
        "test",
        "tests",
        "venv",
        "www",
    }
)
# A package nested deeper than this is vendored, generated, or part of some other
# project that happens to live in the tree.
_MAX_DEPTH = 4
# Every entry shadows site-packages for the whole run, so the list stays short. A
# checkout that wants more than this is a monorepo whose layout we cannot infer.
_MAX_ROOTS = 8


def source_roots(root: Path) -> tuple[str, ...]:
    """Return checkout-relative directories to add to ``PYTHONPATH``, in path order.

    The checkout root itself is never returned: a run already has it as the working
    directory, so adding it changes nothing. An empty result means the layout needs
    no help, which is the common case and leaves the run exactly as it was.

    PEP 420 namespace packages are out of scope. `llama_index/core/__init__.py` with
    no `llama_index/__init__.py` is structurally identical to `src/mypkg/__init__.py`,
    and the two want opposite answers: `src` is the source root, `llama_index` is part
    of the importable name. Nothing in the directory tree distinguishes them, so this
    reports the parent in both cases rather than guessing and getting `src` wrong.
    """
    roots = {package.parent for package in _top_level_packages(root) if package.parent != root}
    relative = sorted(path.relative_to(root) for path in roots)
    usable = [str(path) for path in relative if ":" not in str(path)]
    return tuple(usable[:_MAX_ROOTS])


def _top_level_packages(root: Path) -> list[Path]:
    """Directories that are the outermost regular package of their own subtree.

    The checkout root is never one of them even when it holds an ``__init__.py``: its
    own parent is outside the checkout, and a run already has the root as its working
    directory, so nothing needs adding for it.
    """
    packages: list[Path] = []
    for current, directories, files in os.walk(root):
        here = Path(current)
        depth = len(here.relative_to(root).parts)
        # Pruned rather than filtered afterwards. Descending into node_modules only to
        # discard every result costs the whole subtree, and on a repository with a large
        # one that walk dominates the time to start a run.
        directories[:] = (
            []
            if depth >= _MAX_DEPTH
            else [
                name for name in directories if name not in _NON_SOURCE and not name.startswith(".")
            ]
        )
        if depth == 0 or "__init__.py" not in files:
            continue
        if (here.parent / "__init__.py").is_file():
            continue  # not the outermost package; its parent will be reported
        packages.append(here)
    return packages


def pythonpath(root: Path, *, prefix: str) -> str | None:
    """Join a checkout's source roots into a ``PYTHONPATH`` value under ``prefix``.

    ``prefix`` is where the checkout is visible to the process that will run, which
    is the mount point inside a container and the scratch copy on the host. Returns
    ``None`` when the layout needs no help, so callers can leave the environment
    untouched rather than setting an empty variable.
    """
    roots = source_roots(root)
    if not roots:
        return None
    base = prefix.rstrip("/")
    return ":".join(f"{base}/{name}" for name in roots)
