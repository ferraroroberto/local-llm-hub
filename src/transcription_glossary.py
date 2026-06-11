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

Rules are cached after first load; edit the JSON and restart the hub to
pick up changes (same lifecycle as ``config/models.yaml``).
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GLOSSARY_PATH = PROJECT_ROOT / "config" / "transcription_glossary.json"


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


@lru_cache(maxsize=4)
def load_rules(path: Optional[str] = None) -> Tuple[Rule, ...]:
    """Load and compile the replacement rules from the glossary file.

    Returns an empty tuple if the file is missing or unparseable — a
    broken glossary must never break transcription.
    """
    target = Path(path) if path else DEFAULT_GLOSSARY_PATH
    if not target.exists():
        return tuple()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("⚠️ could not load transcription glossary %s: %s", target, exc)
        return tuple()
    replacements = data.get("replacements", []) if isinstance(data, dict) else []
    return tuple(_compile_rules(replacements))


def load_boost_terms(path: Optional[str] = None) -> List[str]:
    """Return the ``boost_terms`` vocabulary list (issue #91).

    Kept here so the dictionary has a single loader; empty list if the
    file is missing, unparseable, or has no terms.
    """
    target = Path(path) if path else DEFAULT_GLOSSARY_PATH
    if not target.exists():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("⚠️ could not load transcription glossary %s: %s", target, exc)
        return []
    terms = data.get("boost_terms", []) if isinstance(data, dict) else []
    return [t for t in terms if isinstance(t, str) and t.strip()]


def apply_rules(text: str, rules: Tuple[Rule, ...]) -> str:
    """Apply every rule, in order, to ``text``."""
    out = text
    for rule in rules:
        out = rule.pattern.sub(rule.replacement, out)
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
