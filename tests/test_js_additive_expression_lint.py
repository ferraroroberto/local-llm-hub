"""Lint: a `(a.x || 0) + (b.y || 0) [+ ...]` business-rule expression must not
be re-typed out by hand a third time anywhere in `app_web/static/`.

The static ratchet's jscpd pass (`scripts/static_ratchet.py`) only flags
duplicate *blocks* of at least 10 lines / 100 tokens (`.fleet.toml
[static_ratchet]`), so it never sees this shape: the same one-line additive
rule retyped across several short, non-contiguous call sites, each occurrence
far too short to trip jscpd's threshold. This test closes that gap for the
general shape -- any additive chain of `(expr.field || 0)` terms with an
identical field signature, repeated 3+ times anywhere under
`app_web/static/` (excluding vendored components, which are copies by
design) -- not any one specific pattern, so a future business rule retyped
the same way fails here too.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from pathlib import Path

from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[1]

# Vendored admin-SPA components are copies by design (same exclusion the
# static ratchet uses, .fleet.toml [static_ratchet]) -- not this lint's concern.
_EXCLUDED_PREFIXES = ("app_web/static/_vendored/",)

# One `(expr.field || 0)` term.
_TERM = r"\(\s*[\w$]+(?:\.\w+)*\.(\w+)\s*\|\|\s*0\)"
# Two or more terms chained by `+` -- a business-rule expression, not a lone
# fallback default.
_CHAIN_RE = re.compile(rf"{_TERM}(?:\s*\+\s*{_TERM})+")

# The repo's own documented convention (global CLAUDE.md "Conventions"):
# three similar lines is where a helper replaces hand-copied ones.
_DUPLICATE_THRESHOLD = 3


def _tracked_js() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "app_web/static/*.js", "app_web/static/**/*.js"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        creationflags=NO_WINDOW,
    ).stdout
    paths = sorted(set(p for p in out.splitlines() if p.strip()))
    return [
        REPO_ROOT / p
        for p in paths
        if not any(p.startswith(prefix) for prefix in _EXCLUDED_PREFIXES)
    ]


_FIELD_RE = re.compile(r"\.(\w+)\s*\|\|\s*0\)")


def _signatures(text: str) -> list[tuple[str, ...]]:
    """Every additive `(expr.field || 0) + ...` chain's field signature."""
    sigs = []
    for match in _CHAIN_RE.finditer(text):
        fields = tuple(sorted(_FIELD_RE.findall(match.group(0))))
        sigs.append(fields)
    return sigs


def test_no_additive_business_rule_retyped_three_times():
    counts: Counter[tuple[str, ...]] = Counter()
    locations: dict[tuple[str, ...], list[str]] = {}
    for path in _tracked_js():
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(REPO_ROOT).as_posix()
        for lineno, line in enumerate(text.splitlines(), start=1):
            for sig in _signatures(line):
                counts[sig] += 1
                locations.setdefault(sig, []).append(f"{rel}:{lineno}")

    offenders = {
        sig: locs for sig, locs in locations.items()
        if counts[sig] >= _DUPLICATE_THRESHOLD
    }
    assert not offenders, (
        "Additive business-rule expression(s) retyped "
        f"{_DUPLICATE_THRESHOLD}+ times instead of a shared helper:\n"
        + "\n".join(f"  {sig}: {locs}" for sig, locs in offenders.items())
    )
