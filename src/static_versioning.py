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
import re
from pathlib import Path
from typing import Dict, Iterable, Optional

_HASH_LEN = 8

_HASHED_SUFFIXES = (".js", ".css")
_SKIP_DIRS = ("vendor",)

# Matches a relative ES-module import and splits it into (prefix, path,
# basename, existing-stamp, close-quote). The path segment allows subdirs
# (e.g. ``./_vendored/icons/``) so a vendored module nested under static/ is
# stamped too — the hash map is basename-keyed, so the lookup is by basename.
_JS_IMPORT_RE = re.compile(
    r"""(from\s*['"])(\.{1,2}/(?:[\w\-.]+/)*)([\w\-.]+\.js)(\?v=[^'"]*)?(['"])"""
)

# The path segment allows subdirs (e.g. ``_vendored/nav/nav-tabs.css``) so a
# vendored stylesheet linked from index.html is stamped too — any user-visible
# asset outside the ?v= scheme rides iOS Safari's heuristic cache across
# deploys (the app-launcher#372 lesson). The hash map is basename-keyed.
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
    """Return ``{filename: fleet_hash}`` for every hashable static file."""
    if not static_dir.exists():
        return {}
    per_file: Dict[str, str] = {}
    for path in _iter_hashable_files(static_dir):
        per_file[path.name] = _short_hash(path.read_bytes())
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


def rewrite_js_imports(body: str, hashes: Dict[str, str]) -> str:
    """Stamp ``?v=<hash>`` onto every ``from './foo.js'`` import."""
    if not hashes:
        return body

    def _sub(match: re.Match) -> str:
        prefix, path, filename, _existing, quote_close = match.group(1, 2, 3, 4, 5)
        stamp = hashes.get(filename)
        if not stamp:
            return match.group(0)
        return f"{prefix}{path}{filename}?v={stamp}{quote_close}"

    return _JS_IMPORT_RE.sub(_sub, body)


def rewrite_index_html(body: str, hashes: Dict[str, str]) -> str:
    """Stamp ``?v=<hash>`` onto every ``/admin/static/<file>.(css|js)`` href/src."""
    if not hashes:
        return body

    def _sub(match: re.Match) -> str:
        attr, quote_open, filename, _existing, quote_close = match.group(1, 2, 3, 4, 5)
        stamp = hashes.get(filename.rsplit("/", 1)[-1])
        if not stamp:
            return match.group(0)
        return f'{attr}={quote_open}/admin/static/{filename}?v={stamp}{quote_close}'

    return _INDEX_ASSET_RE.sub(_sub, body)


def asset_hash_for(hashes: Dict[str, str], name: str) -> Optional[str]:
    if not hashes:
        return None
    return hashes.get(name)
