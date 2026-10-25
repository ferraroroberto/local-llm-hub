"""Structural check for the switch-revert class of bug (audit #850, finding 1).

The fleet switch (``_vendored/switch``) is optimistic-free by convention: a
row's ``onToggle`` applies the change and ``setSwitch()``s to the confirmed
value on success, reverting on failure. ``dormant.js`` reverts to a mutable
value (`m.dormant`) kept current by the success branch; the bug this repro
guards was ``startup.js`` reverting to ``on``, the row's build-time closure
parameter, which the success branch never updates — so a second toggle that
fails after a first one that succeeded painted the switch at the *original*
value, not the *saved* one.

This statically re-derives that defect for every ``switchEl(...)`` call under
``app_web/static`` (vendored components excluded — they own their own
contract): a ``.catch`` that reverts via ``setSwitch(btn, X)`` must have a
matching ``X = next`` assignment somewhere in the same `switchEl(...)` call
(normally the `.then` success branch), proving `X` tracks the saved value
instead of being a stale build-time parameter. No JS parser — plain bracket
balancing over the source text, scoped to this one convention.
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[2] / "app_web" / "static"

_SWITCH_CALL_RE = re.compile(r"switchEl\(")
_CATCH_RE = re.compile(r"\.catch\(")
_REVERT_ARG_RE = re.compile(r"setSwitch\(\s*\w+\s*,\s*([\w.]+)\s*\)")


def _extract_balanced(text: str, start: int) -> str:
    """`text[start:]` up to the bracket at `start` closing, tracking `(`, `{`
    and `[` together (good enough for this codebase's JS formatting)."""
    assert text[start] == "("
    depth = 0
    i = start
    while i < len(text):
        c = text[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    raise ValueError(f"unbalanced brackets from offset {start}")


def _violations_in(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    found = []
    for call_m in _SWITCH_CALL_RE.finditer(text):
        span = _extract_balanced(text, call_m.end() - 1)
        for catch_m in _CATCH_RE.finditer(span):
            catch_span = _extract_balanced(span, catch_m.end() - 1)
            revert_m = _REVERT_ARG_RE.search(catch_span)
            if not revert_m:
                continue
            ident = revert_m.group(1)
            if ident == "next":
                continue  # the just-requested value is always current
            reassigned = re.search(re.escape(ident) + r"\s*=\s*next\b", span)
            if not reassigned:
                found.append(ident)
    return found


def test_every_switch_revert_tracks_the_saved_value_not_a_stale_closure():
    offenders = {}
    for path in sorted(STATIC_DIR.glob("*.js")):
        violations = _violations_in(path)
        if violations:
            offenders[str(path.relative_to(STATIC_DIR.parents[1]))] = violations
    assert not offenders, (
        "a switchEl(...) row reverts on failure to a value the success branch "
        "never updates, so a second failed toggle paints the stale build-time "
        "state instead of the last saved one (see dormant.js for the correct "
        f"pattern): {offenders}"
    )
