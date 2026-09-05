"""Layout discovery for making a checkout able to import its own packages."""

from __future__ import annotations

from pathlib import Path

from exhibit_a.executor.source_roots import pythonpath, source_roots


def _package(root: Path, *parts: str) -> Path:
    package = root.joinpath(*parts)
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("")
    return package


def test_flat_layout_needs_no_help(tmp_path: Path):
    # The run's working directory is the checkout root, so a package sitting there
    # already imports. Returning nothing keeps the common case exactly as it was.
    _package(tmp_path, "mypkg")

    assert source_roots(tmp_path) == ()
    assert pythonpath(tmp_path, prefix="/work") is None


def test_src_layout_contributes_its_source_directory(tmp_path: Path):
    _package(tmp_path, "src", "mypkg")

    assert source_roots(tmp_path) == ("src",)
    assert pythonpath(tmp_path, prefix="/work") == "/work/src"


def test_package_under_an_application_directory_is_found(tmp_path: Path):
    # open-webui keeps open_webui/ under backend/, which no amount of running from
    # the checkout root will import.
    _package(tmp_path, "backend", "open_webui")

    assert source_roots(tmp_path) == ("backend",)


def test_namespace_package_layout_is_a_documented_limitation(tmp_path: Path):
    # llama_index/ carries no __init__.py, so the outermost *regular* package is
    # `core` and we contribute its parent. That makes `import core` work rather than
    # the real name, `llama_index.core`. This shape is indistinguishable from a src/
    # layout by directory structure alone, and getting src/ right matters more, so
    # the limitation is pinned here rather than papered over with a guess.
    _package(tmp_path, "llama-index-core", "llama_index", "core")
    assert not (tmp_path / "llama-index-core" / "llama_index" / "__init__.py").exists()

    assert source_roots(tmp_path) == ("llama-index-core/llama_index",)


def test_nested_packages_report_only_the_outermost(tmp_path: Path):
    _package(tmp_path, "src", "mypkg")
    _package(tmp_path, "src", "mypkg", "inner")

    assert source_roots(tmp_path) == ("src",)


def test_tests_docs_and_build_output_are_never_put_on_the_path(tmp_path: Path):
    # Each of these shadows real dependencies if added, and none of them holds the
    # project's own importable code.
    for directory in ("tests", "docs", "examples", "build", "node_modules", ".venv"):
        _package(tmp_path, directory, "something")

    assert source_roots(tmp_path) == ()


def test_a_directory_name_containing_a_separator_is_dropped(tmp_path: Path):
    # PYTHONPATH is colon-joined, so such a name would silently split into two bogus
    # entries. Untrusted checkouts choose these names.
    _package(tmp_path, "src", "mypkg")
    _package(tmp_path, "we:ird", "other")

    assert source_roots(tmp_path) == ("src",)


def test_deeply_nested_packages_are_treated_as_vendored(tmp_path: Path):
    _package(tmp_path, "a", "b", "c", "d", "e", "vendored")

    assert source_roots(tmp_path) == ()


def test_roots_are_capped_sorted_and_deterministic(tmp_path: Path):
    for index in range(12):
        _package(tmp_path, f"part{index:02d}", f"pkg{index:02d}")

    roots = source_roots(tmp_path)

    assert len(roots) == 8
    assert list(roots) == sorted(roots)
    assert roots == source_roots(tmp_path)


def test_pythonpath_joins_under_the_given_prefix(tmp_path: Path):
    _package(tmp_path, "backend", "one")
    _package(tmp_path, "src", "two")

    assert pythonpath(tmp_path, prefix="/work/") == "/work/backend:/work/src"
