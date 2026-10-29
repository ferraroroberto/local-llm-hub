"""Fails when a `docs/**/*.mmd` diagram carries a `#NNN`-style issue
reference (issue #910 finding: the vendored `docs_ratchet.py` only scans
`.md` files by its own suffix check, so `.mmd` diagrams are a blind spot
the docs ratchet never covers — `docs/architecture.mmd` had accumulated
~58 such references with no gate noticing). A hand-authored diagram is a
present-tense component/runtime map; the story of why a box was added or
moved belongs in git log and the issue, not baked into a node label.

Zero-tolerance, no baseline: unlike the ratchets this repeats the shape
of, a brand-new `#NNN` in a diagram is always a reword, never a legitimate
addition to track, so there is nothing to baseline.

A `#RRGGBB` / `#RGB` Mermaid `classDef` colour (`fill:#2a2f3a,stroke:#555`)
is excluded by requiring the `#` not be immediately preceded by `:` — every
colour literal in this file's dialect follows a `key:` prefix, an issue
reference never does.

Usage: ``python scripts/mmd_history_check.py``
Exit codes: 0 clean, 1 a reference was found, 3 a file could not be read
(NOT CONFIRMED, never folded into a pass).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ISSUE_RE = re.compile(r"(?<![:\w])#\d{2,4}\b")


def scan(docs_dir: Path) -> list[tuple[str, int, str]]:
    """Every `#NNN`-shaped hit in every `*.mmd` file under ``docs_dir``."""
    hits: list[tuple[str, int, str]] = []
    for path in sorted(docs_dir.rglob("*.mmd")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"mmd history check: FAIL: could not read {rel}: {exc}")
            sys.exit(3)
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in ISSUE_RE.finditer(line):
                hits.append((rel, lineno, match.group(0)))
    return hits


def main() -> int:
    hits = scan(REPO_ROOT / "docs")
    if not hits:
        print("mmd history check: PASS -- no docs/**/*.mmd carries an issue-number reference")
        return 0
    print(
        "mmd history check: FAIL -- issue-number reference(s) found in a .mmd diagram "
        "(docs_ratchet.py only scans .md; strip the history, it belongs in git log / the issue):"
    )
    for rel, lineno, token in hits:
        print(f"  {rel}:{lineno}: {token}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
