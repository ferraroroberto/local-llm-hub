"""Unit tests for fleet-hash cache-busting, including subdirectory assets.

Covers issue #227: the hash map is keyed by static-dir-relative posix path
(not bare basename), so two files sharing a basename in different
directories get distinct keys and each resolves correctly from its own
importer — including a ``../`` parent-relative import from within a nested
vendored component.
"""

from __future__ import annotations

from pathlib import Path

from src.static_versioning import (
    compute_asset_hashes,
    rewrite_index_html,
    rewrite_js_imports,
)


def _make_static_tree(tmp_path: Path) -> Path:
    static_dir = tmp_path / "static"
    (static_dir / "_vendored" / "nav").mkdir(parents=True)
    (static_dir / "_vendored" / "icons").mkdir(parents=True)
    (static_dir / "_vendored" / "empty-state").mkdir(parents=True)
    (static_dir / "main.js").write_text(
        "import { icon } from './_vendored/icons/icons.js';\n", encoding="utf-8"
    )
    (static_dir / "_vendored" / "nav" / "nav-tabs.css").write_text(
        "nav{}", encoding="utf-8"
    )
    (static_dir / "_vendored" / "icons" / "icons.js").write_text(
        "export const icon = 1;", encoding="utf-8"
    )
    (static_dir / "_vendored" / "empty-state" / "empty-state.js").write_text(
        "import { icon } from '../icons/icons.js';\n", encoding="utf-8"
    )
    return static_dir


def test_rewrite_index_html_stamps_vendored_subdir_css(tmp_path: Path) -> None:
    static_dir = _make_static_tree(tmp_path)
    hashes = compute_asset_hashes(static_dir)
    body = '<link href="/admin/static/_vendored/nav/nav-tabs.css" rel="stylesheet">'
    stamped = rewrite_index_html(body, hashes)
    assert "/admin/static/_vendored/nav/nav-tabs.css?v=" in stamped


def test_rewrite_js_imports_stamps_subdir_import_from_root(tmp_path: Path) -> None:
    static_dir = _make_static_tree(tmp_path)
    hashes = compute_asset_hashes(static_dir)
    body = "import { icon } from './_vendored/icons/icons.js';\n"
    stamped = rewrite_js_imports(body, hashes, from_dir="")
    assert "./_vendored/icons/icons.js?v=" in stamped


def test_rewrite_js_imports_stamps_parent_relative_import_from_nested_component(
    tmp_path: Path,
) -> None:
    """A ``../`` import from within a nested vendored component's own
    directory resolves against that directory, not the static root."""
    static_dir = _make_static_tree(tmp_path)
    hashes = compute_asset_hashes(static_dir)
    body = "import { icon } from '../icons/icons.js';\n"
    stamped = rewrite_js_imports(body, hashes, from_dir="_vendored/empty-state")
    assert "../icons/icons.js?v=" in stamped
    assert stamped.count("?v=") == 1
    # Regression guard: the stamp must be the icons.js hash, not a missed
    # lookup that leaves the import unstamped.
    assert f"?v={hashes['_vendored/icons/icons.js']}" in stamped


def test_compute_asset_hashes_keyed_by_relpath_avoids_basename_collision(
    tmp_path: Path,
) -> None:
    """Two files sharing a basename in different directories must not
    collapse into a single hash-map entry (issue #227)."""
    static_dir = tmp_path / "static"
    (static_dir / "a").mkdir(parents=True)
    (static_dir / "b").mkdir(parents=True)
    (static_dir / "a" / "icons.js").write_text("a", encoding="utf-8")
    (static_dir / "b" / "icons.js").write_text("b", encoding="utf-8")
    hashes = compute_asset_hashes(static_dir)
    assert set(hashes) == {"a/icons.js", "b/icons.js"}


def test_same_basename_imports_resolve_from_their_own_directory(
    tmp_path: Path,
) -> None:
    static_dir = tmp_path / "static"
    (static_dir / "a").mkdir(parents=True)
    (static_dir / "b").mkdir(parents=True)
    (static_dir / "a" / "icons.js").write_text("a", encoding="utf-8")
    (static_dir / "b" / "icons.js").write_text("b", encoding="utf-8")
    hashes = compute_asset_hashes(static_dir)

    body = "import { X } from './icons.js';\n"
    stamped_a = rewrite_js_imports(body, hashes, from_dir="a")
    stamped_b = rewrite_js_imports(body, hashes, from_dir="b")

    assert f"?v={hashes['a/icons.js']}" in stamped_a
    assert f"?v={hashes['b/icons.js']}" in stamped_b


def test_vendor_dir_still_unstamped(tmp_path: Path) -> None:
    static_dir = tmp_path / "static"
    (static_dir / "vendor").mkdir(parents=True)
    (static_dir / "vendor" / "chart.js").write_text("chart", encoding="utf-8")
    hashes = compute_asset_hashes(static_dir)
    body = '<script src="/admin/static/vendor/chart.js"></script>'
    assert rewrite_index_html(body, hashes) == body
