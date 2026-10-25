"""Lint: a doc must not claim a model is eager/always-resident when
`config/models.yaml` says it is `startup: on_demand` (audit #855).

The recurring documentation-audit bucket keeps catching the same shape of
drift: a doc states a model's lifecycle in prose, the registry's `startup:`
field changes later, and nothing re-checks the prose. `whisper_translate`
flipped to `startup: on_demand` (#530) while several doc locations kept
calling it eager-loaded / always-ready; this test is proven (by `git stash`
over the pre-fix tree) to fail on that drift, and it fails the same way on
any future model whose doc prose and registry disagree, not just this one id.

Granularity, deliberately structural rather than a fixed character window
(a window version of this test flagged a dozen *unrelated* models the first
time it ran against this same pre-fix tree -- long tables and dense bullets
put unrelated model names within any fixed distance of an unrelated claim):
a fenced-code line, a `| ... |` table row, and a top-level/nested bullet item
are each their own unit; a model name and an eager-claim phrase must land in
the *same* unit to count as a claim about that model.

Scope, deliberately narrow to stay low-false-positive: only the "doc claims
eager/always-resident but the registry says on_demand" direction, since that
is the direction that actually regressed. The reverse (doc calls an eager
model "on-demand") is not checked -- docs legitimately describe the
*mechanism* ("flip eager <-> on_demand") without asserting a specific
model's current state.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src import model_registry

REPO_ROOT = Path(__file__).resolve().parents[2]

# Phrases a doc uses to assert a model loads eagerly / stays resident. Matched
# case-insensitively; this is the exact vocabulary the audit found stale.
_EAGER_CLAIM_RE = re.compile(
    r"eager-loaded|eager-load|eager cpu|runs eager|always resident|always ready",
    re.IGNORECASE,
)

# A new bullet (top-level or nested) starts a fresh prose unit even with no
# blank line before it -- markdown renders consecutive `- ` items as separate
# list entries whether or not the source has a blank line between them, and
# treating a whole bulleted section as one block is what produced the
# dozen-model false-positive run noted above.
_BULLET_START_RE = re.compile(r"\n(?=[ \t]*(?:[-*]|\d+\.)[ \t])")

# Generated/dated research snapshots are out of scope for both the docs
# ratchet and this lint -- they are a point-in-time record, not maintained
# prose (see .fleet.toml [docs_ratchet]).
_EXCLUDED_PREFIXES = ("docs/frontier/runs/",)


def _tracked_docs() -> list[Path]:
    import subprocess

    from src.no_window import NO_WINDOW

    out = subprocess.run(
        # Two patterns, not one: git's `**` pathspec glob requires at least
        # one directory component, so `docs/**/*.md` alone silently skips
        # direct children like `docs/running.md` (proven the hard way --
        # the first version of this lint matched zero of the six locations).
        ["git", "ls-files", "docs/*.md", "docs/**/*.md", "README.md"],
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


def _claim_units(text: str) -> list[str]:
    """Split a doc into independent claim-bearing chunks.

    A fenced-code line and a `| ... |` table row are each their own unit (one
    launcher line or one table row is one fact). Everything else is grouped
    into blank-line paragraphs, then split again on each new bullet start.
    """
    units: list[str] = []
    prose_buf: list[str] = []
    in_fence = False

    def flush_prose() -> None:
        if not prose_buf:
            return
        block = "\n".join(prose_buf)
        prose_buf.clear()
        for para in re.split(r"\n\s*\n", block):
            if para.strip():
                units.extend(c for c in _BULLET_START_RE.split(para) if c.strip())

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            flush_prose()
            in_fence = not in_fence
            continue
        if in_fence:
            units.append(line)
            continue
        if stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 2:
            flush_prose()
            units.append(line)
            continue
        prose_buf.append(line)
    flush_prose()
    return units


def test_no_doc_claims_an_on_demand_model_is_eager():
    on_demand_models = [
        m
        for m in model_registry.all_models(apply_cpu_offload=False)
        if m.startup == model_registry.STARTUP_ON_DEMAND
    ]
    assert on_demand_models, (
        "no on_demand model found in config/models.yaml -- this probably "
        "means the registry load broke, not that the fleet went all-eager"
    )

    needles: list[tuple[str, re.Pattern]] = []
    for m in on_demand_models:
        names = {m.id, m.display_name}
        pattern = re.compile(
            r"\b(" + "|".join(re.escape(n) for n in names if n) + r")\b"
        )
        needles.append((m.id, pattern))

    failures: list[str] = []
    for doc_path in _tracked_docs():
        text = doc_path.read_text(encoding="utf-8")
        rel = doc_path.relative_to(REPO_ROOT).as_posix()
        for unit in _claim_units(text):
            if not _EAGER_CLAIM_RE.search(unit):
                continue
            for model_id, name_pattern in needles:
                if name_pattern.search(unit):
                    failures.append(
                        f"{rel}: claims `{model_id}` is eager/always-resident, "
                        f"but config/models.yaml has startup: on_demand for it "
                        f"-- unit: {unit.strip()[:200]!r}"
                    )

    assert not failures, "stale eager-load claim(s):\n" + "\n".join(failures)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
