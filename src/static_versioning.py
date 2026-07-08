"""Content-hash stamping for /admin/static assets.

Ported from app-launcher. The webapp ships ``index.html`` + a handful of
ES-module ``.js`` files + ``styles.css``; iOS Safari (and especially the
standalone PWA) caches them aggressively, so a deploy isn't really "live"
until the cached copies are evicted. To make that deterministic we
append ``?v=<hash>`` to every asset URL. The hash is computed once at
app startup; tray-restart-on-edit is the project convention, so we don't
need a watcher.

We use a single **fleet hash** — sha256 over the concatenation of each
file's per-file hash, sorted by name. Reasons:

  * The ES-module graph is small (~6 files); per-file transitive hashing
    would need SCC handling — overkill.
  * One value to log and to surface from ``/admin/api/version`` for a
    visual diff against the deployed build.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from pathlib import Path
from typing import Dict, Iterable, Optional

_HASH_LEN = 8

_HASHED_SUFFIXES = (".js", ".css")
_SKIP_DIRS = ("vendor",)

# Matches a relative ES-module import and splits it into (prefix, path,
# basename, existing-stamp, close-quote). The path segment allows subdirs
# (e.g. ``./_vendored/icons/``) so a vendored module nested under static/ is
# stamped too — the hash map is keyed by static-dir-relative path, and the
# ``./``/``../`` specifier is resolved against the importing file's own
# directory before the lookup (see ``_resolve_specifier``).
_JS_IMPORT_RE = re.compile(
    r"""(from\s*['"])(\.{1,2}/(?:[\w\-.]+/)*)([\w\-.]+\.js)(\?v=[^'"]*)?(['"])"""
)

# The path segment allows subdirs (e.g. ``_vendored/nav/nav-tabs.css``) so a
# vendored stylesheet linked from index.html is stamped too — any user-visible
# asset outside the ?v= scheme rides iOS Safari's heuristic cache across
# deploys (the app-launcher#372 lesson). The captured group is already the
# static-dir-relative path, so it maps directly onto a ``hashes`` key.
_INDEX_ASSET_RE = re.compile(
    r"""(href|src)=(['"])/admin/static/((?:[\w\-.]+/)*[\w\-.]+\.(?:css|js))(\?v=[^'"]*)?(['"])"""
)


def _short_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:_HASH_LEN]


def _iter_hashable_files(static_dir: Path) -> Iterable[Path]:
    for path in sorted(static_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(static_dir).parts[:-1]):
            continue
        if path.suffix.lower() not in _HASHED_SUFFIXES:
            continue
        yield path


def compute_asset_hashes(static_dir: Path) -> Dict[str, str]:
    """Return ``{relpath: fleet_hash}`` for every hashable static file.

    Keyed by the file's static-dir-relative posix path (e.g.
    ``_vendored/icons/icons.js``), not the bare basename — two files
    sharing a basename in different directories (e.g. two vendored
    components each shipping their own ``icons.js``) must get distinct
    keys, or one silently gets the other's stamp.
    """
    if not static_dir.exists():
        return {}
    per_file: Dict[str, str] = {}
    for path in _iter_hashable_files(static_dir):
        relpath = path.relative_to(static_dir).as_posix()
        per_file[relpath] = _short_hash(path.read_bytes())
    if not per_file:
        return {}
    fleet_input = "\n".join(
        f"{name}:{per_file[name]}" for name in sorted(per_file)
    ).encode("utf-8")
    fleet_hash = _short_hash(fleet_input)
    return {name: fleet_hash for name in per_file}


def fleet_hash_of(hashes: Dict[str, str]) -> str:
    if not hashes:
        return ""
    return next(iter(hashes.values()))


def _resolve_specifier(from_dir: str, spec: str) -> str:
    """Resolve a ``./``/``../`` import specifier against ``from_dir``.

    ``from_dir`` is the static-dir-relative posix directory of the file
    doing the importing (empty string at the static root). Returns the
    static-dir-relative posix path used as the ``hashes`` lookup key,
    e.g. ``_resolve_specifier("_vendored/empty-state", "../icons/icons.js")
    == "_vendored/icons/icons.js"``.
    """
    joined = posixpath.join(from_dir, spec) if from_dir else spec
    return posixpath.normpath(joined)


def rewrite_js_imports(body: str, hashes: Dict[str, str], from_dir: str = "") -> str:
    """Stamp ``?v=<hash>`` onto every ``from './foo.js'`` import.

    ``from_dir`` is the static-dir-relative posix directory of the file
    being rewritten (empty string for a file at the static root) — needed
    to resolve ``./``/``../`` specifiers (including into subdirectories,
    e.g. ``./_vendored/icons/icons.js``) against ``hashes``, which is
    keyed by static-dir-relative path, not bare basename. Imports with no
    matching entry are left alone.
    """
    if not hashes:
        return body

    def _sub(match: re.Match) -> str:
        prefix, path, filename, _existing, quote_close = match.group(1, 2, 3, 4, 5)
        spec = f"{path}{filename}"
        stamp = hashes.get(_resolve_specifier(from_dir, spec))
        if not stamp:
            return match.group(0)
        return f"{prefix}{spec}?v={stamp}{quote_close}"

    return _JS_IMPORT_RE.sub(_sub, body)


def rewrite_index_html(body: str, hashes: Dict[str, str]) -> str:
    """Stamp ``?v=<hash>`` onto every ``/admin/static/<file>.(css|js)`` href/src.

    ``filename`` may include subdirectories (e.g.
    ``_vendored/nav/nav-tabs.css``) and maps directly onto a ``hashes``
    key — no resolution needed, unlike a relative JS import.
    """
    if not hashes:
        return body

    def _sub(match: re.Match) -> str:
        attr, quote_open, filename, _existing, quote_close = match.group(1, 2, 3, 4, 5)
        stamp = hashes.get(filename)
        if not stamp:
            return match.group(0)
        return f'{attr}={quote_open}/admin/static/{filename}?v={stamp}{quote_close}'

    return _INDEX_ASSET_RE.sub(_sub, body)


def asset_hash_for(hashes: Dict[str, str], name: str) -> Optional[str]:
    if not hashes:
        return None
    return hashes.get(name)
