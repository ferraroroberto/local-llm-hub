"""Post-process whisper transcripts through a committed glossary.

This implements the **replacement-rules half** of a Wispr-Flow-style
dictionary (issue #90). The two-part dictionary lives in one committed
file, ``config/transcription_glossary.json``:

  * ``replacements`` — an *ordered* list of literal ``{"from", "to"}``
    rules applied to the transcript text **after** whisper returns it.
    This deterministically fixes acoustically-strong errors that
    recognition-level biasing cannot (e.g. "cloud code" → "Claude Code",
    where whisper hears "Claude" as "Cloud" regardless of any prompt).
  * ``boost_terms`` — vocabulary fed to whisper as an initial prompt to
    *bias recognition* (issue #91). Consumed at backend-launch time, not
    here; listed in the same file so the dictionary has one source of
    truth.

The replacement engine is conservative by design: case-insensitive,
word-boundary-anchored, longest-phrase-first, applied in file order.
Multi-word / unambiguous phrases only, so non-listed text is returned
byte-for-byte unchanged.

A third, gitignored source — ``config/aliases.local.json`` — is the shared
alias file: entries ``{canonical, variants[], kind}`` of proper nouns and
their known mishearings. Every canonical joins the boost vocabulary and every
variant becomes a replacement rule to its canonical, so one correction feeds
both halves and every consumer that reads the hub's read-only alias endpoint.
Absent file → behaviour is exactly the two-file dictionary above.

The boost vocabulary is capped to a token budget (:func:`build_boost_plan`)
because whisper honours only the tail of a long initial prompt.

Rules are cached after first load; edit the JSON and restart the hub to
pick up changes (same lifecycle as ``config/models.yaml``).
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from ..atomic_write import atomic_write_text, read_json

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GLOSSARY_PATH = PROJECT_ROOT / "config" / "transcription_glossary.json"
DEFAULT_LOCAL_BOOST_PATH = PROJECT_ROOT / "config" / "transcription_glossary.local.json"
DEFAULT_ALIAS_PATH = PROJECT_ROOT / "config" / "aliases.local.json"

# Whisper honours only the last ~224 initial-prompt tokens; 200 leaves headroom
# for the estimate being approximate. Overridable via ``boost_token_budget``.
DEFAULT_BOOST_TOKEN_BUDGET = 200
_PROMPT_PREFIX = "Glossary: "
_PROMPT_SUFFIX = "."
_CHARS_PER_TOKEN = 3


class Rule(NamedTuple):
    """A compiled replacement rule: word-boundary pattern → literal text."""

    pattern: "re.Pattern[str]"
    replacement: str


def _compile_rules(replacements: List[Dict[str, str]]) -> List[Rule]:
    """Compile raw ``{"from", "to"}`` dicts into ordered :class:`Rule`s.

    Longest source phrase first so a short rule can never pre-empt a
    longer overlapping one; ties preserve file order (stable sort).
    """
    valid = [
        r for r in replacements
        if isinstance(r, dict) and r.get("from") and isinstance(r.get("to"), str)
    ]
    ordered = sorted(valid, key=lambda r: len(r["from"]), reverse=True)
    rules: List[Rule] = []
    for r in ordered:
        # \b…\b word-boundary anchor + case-insensitive literal match.
        pattern = re.compile(rf"\b{re.escape(r['from'])}\b", re.IGNORECASE)
        rules.append(Rule(pattern, r["to"]))
    return rules


def load_aliases(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return the shared alias entries, normalised to ``{canonical, variants, kind}``.

    The file is ``{"aliases": [...]}`` (a bare list is accepted too). Entries
    without a canonical are dropped; variants are trimmed, de-duplicated
    (case-insensitive) and exclude the canonical itself; ``kind`` defaults to
    ``"term"``. Missing, unparseable or oddly shaped file → empty list, so a
    broken alias file can never break transcription.
    """
    target = Path(path) if path else DEFAULT_ALIAS_PATH
    data = read_json(target, "alias file")
    raw = data.get("aliases", []) if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []

    entries: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        canonical = str(item.get("canonical") or "").strip()
        if not canonical:
            continue
        variants: List[str] = []
        seen = {canonical.lower()}
        for v in item.get("variants") or []:
            if not isinstance(v, str):
                continue
            variant = v.strip()
            if variant and variant.lower() not in seen:
                seen.add(variant.lower())
                variants.append(variant)
        kind = str(item.get("kind") or "term").strip() or "term"
        entries.append({"canonical": canonical, "variants": variants, "kind": kind})
    return entries


@lru_cache(maxsize=4)
def load_rules(
    path: Optional[str] = None, alias_path: Optional[str] = None
) -> Tuple[Rule, ...]:
    """Load and compile the replacement rules from the glossary + alias files.

    Each alias variant becomes a ``variant → canonical`` rule, merged after the
    glossary's own ``replacements``. Returns an empty tuple if neither file
    yields rules — a broken glossary must never break transcription.
    """
    target = Path(path) if path else DEFAULT_GLOSSARY_PATH
    replacements: List[Dict[str, str]] = []
    if target.exists():
        data = read_json(target, "transcription glossary")
        if isinstance(data, dict):
            replacements = list(data.get("replacements", []))
    for entry in load_aliases(alias_path):
        replacements.extend(
            {"from": variant, "to": entry["canonical"]} for variant in entry["variants"]
        )
    return tuple(_compile_rules(replacements))


def load_boost_terms(
    path: Optional[str] = None,
    local_path: Optional[str] = None,
    alias_path: Optional[str] = None,
) -> List[str]:
    """Return the ``boost_terms`` vocabulary list (issue #91).

    Kept here so the dictionary has a single loader; empty list if the
    file is missing, unparseable, or has no terms.

    Also merges an optional gitignored local overlay
    (``config/transcription_glossary.local.json`` by default, issue #290) —
    same ``{"boost_terms": [...]}`` shape, for vocabulary that must never
    land in this public repo (e.g. a caller's private proper nouns).
    Missing overlay file is a silent no-op. The overlay is **not** visible
    to :func:`load_glossary`/:func:`save_glossary` (the admin editor round
    trip), so editing the committed dictionary in-app can never leak or
    overwrite it. The alias file (:func:`load_aliases`) is likewise outside
    the editor round trip.
    """
    target = Path(path) if path else DEFAULT_GLOSSARY_PATH
    terms: List[str] = []
    data = read_json(target, "transcription glossary")
    raw = data.get("boost_terms", []) if isinstance(data, dict) else []
    terms.extend(t for t in raw if isinstance(t, str) and t.strip())

    local_target = Path(local_path) if local_path else DEFAULT_LOCAL_BOOST_PATH
    local_data = read_json(local_target, "local transcription glossary")
    raw_local = local_data.get("boost_terms", []) if isinstance(local_data, dict) else []
    terms.extend(t for t in raw_local if isinstance(t, str) and t.strip())

    # Alias canonicals join last; a canonical already listed (any case) is
    # skipped so the launch prompt never repeats a term.
    known = {t.lower() for t in terms}
    for entry in load_aliases(alias_path):
        if entry["canonical"].lower() not in known:
            known.add(entry["canonical"].lower())
            terms.append(entry["canonical"])

    return terms


class BoostPlan(NamedTuple):
    """The launch prompt after ranking and capping to the token budget."""

    prompt: str
    kept: List[str]
    dropped: List[str]
    used_tokens: int
    total_tokens: int
    budget: int


def estimate_tokens(text: str) -> int:
    """Cheap token estimate: ``ceil(len / 3)``.

    English averages ~4 chars/token but proper nouns and jargon split finer, so
    3 deliberately over-counts: an under-count would let whisper silently drop
    the oldest prompt terms instead of us dropping the lowest-ranked ones.
    """
    return -(-len(text) // _CHARS_PER_TOKEN)


def _prompt_tokens(terms: List[str]) -> int:
    """Estimated tokens of the prompt rendered from ``terms`` (+1 per ", ")."""
    frame = estimate_tokens(_PROMPT_PREFIX + _PROMPT_SUFFIX)
    return frame + sum(estimate_tokens(t) + 1 for t in terms)


def _configured_budget(target: Path) -> Optional[int]:
    """The glossary file's own ``boost_token_budget``, if it is a valid int."""
    data = read_json(target, "transcription glossary")
    value = data.get("boost_token_budget") if isinstance(data, dict) else None
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def load_boost_budget(path: Optional[str] = None) -> int:
    """Token budget for the boost prompt (file value, else the default)."""
    target = Path(path) if path else DEFAULT_GLOSSARY_PATH
    return _configured_budget(target) or DEFAULT_BOOST_TOKEN_BUDGET


def rank_boost_terms(terms: List[str], aliases: List[Dict[str, Any]]) -> List[str]:
    """Order ``terms`` by how much a boost is worth, highest first.

    The only usage signal stored is the alias file: a canonical with more known
    mishearings is one whisper demonstrably gets wrong, so it ranks first.
    Everything else keeps its file order (the sort is stable), so the ranking
    is deterministic.
    """
    mishears = {e["canonical"].lower(): len(e["variants"]) for e in aliases}
    return sorted(terms, key=lambda t: -mishears.get(t.lower(), 0))


def build_boost_plan(
    path: Optional[str] = None,
    local_path: Optional[str] = None,
    alias_path: Optional[str] = None,
    budget: Optional[int] = None,
) -> BoostPlan:
    """Fit the boost vocabulary into the token budget.

    Whisper honours only the last ~224 prompt tokens, so an over-long list
    would lose terms at random. When everything fits the prompt keeps file
    order untouched; otherwise the top-ranked terms are kept (a strict prefix
    of the ranking, so a short low-ranked term never jumps a long one) and the
    survivors are re-emitted in file order.
    """
    terms = load_boost_terms(path, local_path, alias_path)
    limit = budget if budget is not None else load_boost_budget(path)
    total = _prompt_tokens(terms) if terms else 0

    kept = terms
    if total > limit:
        keep = set()
        used = _prompt_tokens([])
        for term in rank_boost_terms(terms, load_aliases(alias_path)):
            used += estimate_tokens(term) + 1
            if used > limit:
                break
            keep.add(term)
        kept = [t for t in terms if t in keep]

    kept_set = set(kept)
    dropped = [t for t in terms if t not in kept_set]
    prompt = _PROMPT_PREFIX + ", ".join(kept) + _PROMPT_SUFFIX if kept else ""
    used_tokens = _prompt_tokens(kept) if kept else 0
    return BoostPlan(prompt, kept, dropped, used_tokens, total, limit)


def load_glossary(path: Optional[str] = None) -> Dict[str, Any]:
    """Return the raw, editable glossary: ``{replacements, boost_terms}``.

    Unlike :func:`load_rules` (compiled patterns) this returns the plain
    JSON shape the admin editor round-trips. Missing/unparseable file →
    empty lists, never an error.
    """
    target = Path(path) if path else DEFAULT_GLOSSARY_PATH
    if not target.exists():
        return {"replacements": [], "boost_terms": []}
    data = read_json(target, "transcription glossary")
    if not isinstance(data, dict):
        return {"replacements": [], "boost_terms": []}
    return {
        "replacements": data.get("replacements", []) or [],
        "boost_terms": data.get("boost_terms", []) or [],
    }


def normalize_glossary(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate + clean an incoming glossary payload for persistence.

    Drops malformed entries, trims whitespace, and de-dupes ``boost_terms``
    (case-insensitive, order-preserving). Raises :class:`ValueError` if the
    top-level shape is wrong so the API can answer 400 rather than write
    junk that would later be silently ignored by :func:`load_rules`.
    """
    if not isinstance(data, dict):
        raise ValueError("glossary must be a JSON object")
    raw_repl = data.get("replacements", [])
    raw_boost = data.get("boost_terms", [])
    if not isinstance(raw_repl, list) or not isinstance(raw_boost, list):
        raise ValueError("'replacements' and 'boost_terms' must be lists")

    replacements: List[Dict[str, str]] = []
    for r in raw_repl:
        if not isinstance(r, dict):
            continue
        src = str(r.get("from", "")).strip()
        dst = r.get("to", "")
        if not src or not isinstance(dst, str):
            continue
        replacements.append({"from": src, "to": dst})

    boost_terms: List[str] = []
    seen = set()
    for t in raw_boost:
        if not isinstance(t, str):
            continue
        term = t.strip()
        key = term.lower()
        if term and key not in seen:
            seen.add(key)
            boost_terms.append(term)

    return {"replacements": replacements, "boost_terms": boost_terms}


def save_glossary(data: Dict[str, Any], path: Optional[str] = None) -> Dict[str, Any]:
    """Validate, atomically write, and invalidate caches. Returns the saved shape.

    The replacement-rule cache (:func:`load_rules`) is cleared so edits
    take effect on the next request without a hub restart.
    ``boost_terms`` changes only bind on the next whisper launch (boosting
    is a launch-time arg), which the caller surfaces to the user.
    """
    target = Path(path) if path else DEFAULT_GLOSSARY_PATH
    clean = normalize_glossary(data)
    persisted = dict(clean)
    # The editor never sends the budget; carry a hand-set one across the save.
    budget = _configured_budget(target)
    if budget is not None:
        persisted["boost_token_budget"] = budget
    atomic_write_text(
        target, json.dumps(persisted, indent=2, ensure_ascii=False) + "\n"
    )
    load_rules.cache_clear()
    logger.info(
        "💾 Saved transcription glossary (%d replacements, %d boost terms)",
        len(clean["replacements"]), len(clean["boost_terms"]),
    )
    return clean


def apply_rules(text: str, rules: Tuple[Rule, ...]) -> str:
    """Apply every rule, in order, to ``text``."""
    out = text
    for rule in rules:
        # A callable keeps the replacement literal: a string is parsed as a
        # template, so a backslash in a saved rule would raise "bad escape".
        out = rule.pattern.sub(lambda _m, r=rule.replacement: r, out)
    return out


def apply_to_response(
    content: bytes,
    content_type: Optional[str],
    rules: Tuple[Rule, ...],
) -> bytes:
    """Rewrite the transcript text inside a whisper-server response body.

    Handles the OpenAI ``response_format`` shapes whisper-server emits:

      * ``application/json`` → rewrite the top-level ``text`` field and,
        for ``verbose_json``, each ``segments[].text``.
      * ``text/*`` (``response_format=text``/``srt``/``vtt``) → rewrite
        the whole body (word-boundary matching leaves timestamps alone).

    Unknown / binary content types, and bodies that fail to decode or
    parse, are returned byte-for-byte unchanged.
    """
    if not rules or not content:
        return content

    ctype = (content_type or "").lower()
    try:
        if "application/json" in ctype:
            data = json.loads(content.decode("utf-8"))
            if not isinstance(data, dict):
                return content
            touched = False
            if isinstance(data.get("text"), str):
                data["text"] = apply_rules(data["text"], rules)
                touched = True
            segments = data.get("segments")
            if isinstance(segments, list):
                for seg in segments:
                    if isinstance(seg, dict) and isinstance(seg.get("text"), str):
                        seg["text"] = apply_rules(seg["text"], rules)
                        touched = True
            if not touched:
                return content
            return json.dumps(data, ensure_ascii=False).encode("utf-8")

        if ctype.startswith("text/"):
            return apply_rules(content.decode("utf-8"), rules).encode("utf-8")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning(
            "⚠️ transcription glossary skipped (unparseable response): %s", exc
        )
        return content

    return content
