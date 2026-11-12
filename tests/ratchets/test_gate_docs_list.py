"""CLAUDE.md names every ratchet the gate runs, and the README keeps no second list.

`scripts/verify-before-ship.ps1` is the source of truth for which ratchets run;
CLAUDE.md's Verification gate section is the one prose list of them. A ratchet
added to the gate without a CLAUDE.md entry, or a README that re-documents the
ratchets one by one, is how the two copies drifted apart before.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "verify-before-ship.ps1"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
README = REPO_ROOT / "README.md"


def _gate_ratchet_steps() -> list[str]:
    text = GATE.read_text(encoding="utf-8")
    return re.findall(r'^Step "([a-z -]+ ratchet)"', text, re.MULTILINE)


def _gate_section() -> str:
    text = CLAUDE_MD.read_text(encoding="utf-8")
    match = re.search(r"^## Verification gate\n(.*?)(?=^## )", text, re.DOTALL | re.MULTILINE)
    assert match, "CLAUDE.md lost its Verification gate section"
    return match.group(1).lower()


def test_gate_runs_ratchets() -> None:
    assert _gate_ratchet_steps(), "no `Step \"... ratchet\"` found: the gate's step syntax changed"


def test_claude_md_names_every_gate_ratchet() -> None:
    section = _gate_section()
    missing = [step for step in _gate_ratchet_steps() if step not in section]
    assert not missing, f"CLAUDE.md's Verification gate section does not name: {missing}"


def test_readme_test_section_keeps_no_ratchet_list() -> None:
    text = README.read_text(encoding="utf-8")
    match = re.search(r"^## Test\n(.*?)(?=^## )", text, re.DOTALL | re.MULTILINE)
    assert match, "README.md lost its Test section"
    scripts = re.findall(r"scripts[\\/]\w+_ratchet", match.group(1))
    assert not scripts, f"README's Test section re-documents ratchets (CLAUDE.md owns them): {scripts}"
