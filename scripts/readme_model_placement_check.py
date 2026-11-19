"""Fails when README.md's ``## Setup`` section restates per-model/per-host
GGUF sizes in prose — the signature shape of placement drift: a sentence
naming several models each followed by a parenthetical size, e.g. ``Qwen 3.5 4B (~2.6 GB), Gemma 4 26B-A4B IQ4_XS
(~13.4 GB)``. This repo's own CLAUDE.md reserves model **placement** (which
host owns which row, and by extension what that host's rotation currently
is) to `config/models.yaml` alone -- "never restate it in a doc" -- because
that list drifts out of date on every rotation while the doc sits still.
README.md is the headline setup surface a new reader hits first, so this
check is scoped there rather than across every `docs/**/*.md` file, most of
which carry per-model facts (benchmarks, VRAM budgets) as their actual
subject rather than a restated host rotation, and would need a much fussier
signal to separate the two without false positives.

Signal: two or more ``(~<number> GB|MB)`` parentheticals inside one
paragraph of the ``## Setup`` section. A single incidental size mention (the
TTS extras' "~2 GB" torch pull, unparenthesized) never trips this; a
paragraph that *enumerates* several sized models is always a restated
rotation list, which belongs in `config/models.yaml` and a link to it, not
here.

Zero-tolerance, no baseline: like `mmd_history_check.py`, a fresh hit here is
always a reword (replace the list with one line pointing at
`config/models.yaml`'s `enabled:` list), never a legitimate addition to
track.

Usage: ``python scripts/readme_model_placement_check.py``
Exit codes: 0 clean, 1 a restated size list was found, 3 README.md could not
be read (NOT CONFIRMED, never folded into a pass).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
SIZE_RE = re.compile(r"\(~\d+(?:\.\d+)?\s*(?:GB|MB)\)")
SECTION_START_RE = re.compile(r"^## Setup\s*$")
SECTION_END_RE = re.compile(r"^## (?!Setup)")
MIN_HITS_PER_PARAGRAPH = 2


def setup_section(text: str) -> str:
    """The ``## Setup`` section's body, up to (not including) the next ``##`` heading."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if SECTION_START_RE.match(line):
            start = i + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for i in range(start, len(lines)):
        if SECTION_END_RE.match(lines[i]):
            end = i
            break
    return "\n".join(lines[start:end])


def paragraphs(section: str) -> list[str]:
    """Blocks separated by a blank line, skipping fenced code blocks."""
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in section.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.strip() == "":
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def scan(text: str) -> list[tuple[str, int]]:
    """(excerpt, hit-count) for every Setup-section paragraph with >= MIN_HITS_PER_PARAGRAPH sizes."""
    hits: list[tuple[str, int]] = []
    for para in paragraphs(setup_section(text)):
        count = len(SIZE_RE.findall(para))
        if count >= MIN_HITS_PER_PARAGRAPH:
            excerpt = " ".join(para.split())
            hits.append((excerpt[:160], count))
    return hits


def main() -> int:
    try:
        text = README.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"readme model placement check: FAIL: could not read README.md: {exc}")
        return 3
    hits = scan(text)
    if not hits:
        print(
            "readme model placement check: PASS -- README.md's Setup section "
            "names no restated per-model size list"
        )
        return 0
    print(
        "readme model placement check: FAIL -- README.md's Setup section restates "
        "per-model/per-host sizes that belong only in config/models.yaml:"
    )
    for excerpt, count in hits:
        print(f"  {count} size annotations: {excerpt}...")
    return 1


if __name__ == "__main__":
    sys.exit(main())
