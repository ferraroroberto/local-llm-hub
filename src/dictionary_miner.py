"""Mine recent dictation transcripts for glossary suggestions (issue #94).

The miner reads the last *N* days of real transcripts from
voice-transcriber's **session API** over loopback (the canonical corpus
owner — local-llm-hub stores no transcript text of its own) and proposes
two kinds of dictionary update for review:

  * candidate ``boost_terms`` — recurring proper-noun / multi-word domain
    vocabulary whisper is likely to mis-hear, found with cheap
    frequency + capitalisation heuristics.
  * candidate ``replacements`` — frequent mis-transcriptions mapping to a
    canonical term, clustered by an **optional** LLM pass that runs
    *through the hub itself* (so no new provider dependency). The pass
    degrades gracefully to heuristics-only if the hub call fails.

Nothing here is ever auto-applied: :func:`mine_suggestions` returns
suggestions for the admin editor to accept/reject.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from src.transcription_glossary import load_glossary

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "dictionary_miner.json"

_DEFAULTS: Dict[str, Any] = {
    "voice_transcriber_base_url": "https://127.0.0.1:8443",
    "default_days": 7,
    "max_tokens": 60000,
    "min_count": 2,
    "use_llm": True,
    "llm_model": "claude-haiku-4-5",
}

# Common words that are capitalised at sentence start but are not domain
# vocabulary — excluded so the heuristic doesn't propose "The", "And", …
_STOPWORDS = {
    "the", "and", "but", "for", "you", "your", "this", "that", "with",
    "have", "has", "had", "are", "was", "were", "will", "would", "could",
    "should", "they", "them", "then", "than", "what", "when", "where",
    "which", "who", "how", "why", "yes", "not", "now", "out", "get", "got",
    "can", "did", "does", "from", "into", "just", "like", "make", "made",
    "okay", "ok", "yeah", "i", "im", "ive", "its", "it", "a", "an", "to",
    "of", "in", "on", "is", "be", "we", "so", "if", "or", "at", "by", "as",
    "do", "go", "no", "up", "me", "my", "he", "she", "us", "all", "any",
    "one", "two", "let", "see", "say", "use", "want", "need", "know",
    "think", "going", "really", "actually", "basically", "something",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
}


class MinerError(RuntimeError):
    """Raised when the corpus can't be reached or read."""


def load_miner_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Load miner config, layering the file (if any) over baked defaults."""
    target = Path(path) if path else DEFAULT_CONFIG_PATH
    cfg = dict(_DEFAULTS)
    if target.exists():
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg.update({k: v for k, v in raw.items() if v is not None})
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("⚠️ could not read miner config %s: %s", target, exc)
    return cfg


# --------------------------------------------------------------- corpus fetch


async def fetch_transcripts(
    base_url: str, days: int, max_tokens: int
) -> Tuple[List[str], Optional[str]]:
    """Pull the last ``days`` of transcripts from the session API.

    Uses the bulk export (``GET /api/sessions/transcripts``) so it's one
    round trip, not N+1. Same-host loopback bypasses auth; the self-signed
    cert means ``verify=False``. Returns ``(transcripts, vt_git_sha)``,
    truncating once the cumulative word budget (~``max_tokens``) is hit.
    Raises :class:`MinerError` on any transport/HTTP failure.
    """
    base = base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            resp = await client.get(
                f"{base}/api/sessions/transcripts", params={"days": days}
            )
            resp.raise_for_status()
            body = resp.json()
            sha = None
            try:
                vresp = await client.get(f"{base}/api/version")
                if vresp.is_success:
                    sha = vresp.json().get("git_sha")
            except httpx.HTTPError:
                sha = None
    except httpx.HTTPError as exc:
        raise MinerError(
            f"could not reach the transcript corpus at {base} ({exc}). "
            "Is voice-transcriber running?"
        ) from exc

    entries = body.get("transcripts", []) if isinstance(body, dict) else []
    transcripts: List[str] = []
    budget = 0
    for e in entries:
        text = e.get("transcript") if isinstance(e, dict) else None
        if not isinstance(text, str) or not text.strip():
            continue
        transcripts.append(text)
        budget += len(text.split())
        if budget >= max_tokens:
            break
    return transcripts, sha


# ----------------------------------------------------------- heuristic mining

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?\n]+")


def _is_candidate_token(tok: str) -> bool:
    bare = tok.replace("'", "").replace("’", "").replace("-", "")
    return len(bare) >= 3 and bare.isalpha() and bare.lower() not in _STOPWORDS


def heuristic_candidates(
    transcripts: List[str], existing_boost: List[str], min_count: int = 2
) -> List[Dict[str, Any]]:
    """Frequency + capitalisation heuristic for candidate boost terms.

    A candidate is a capitalised, non-sentence-initial token (or a run of
    them — e.g. "Claude Code") that recurs ``>= min_count`` times and is
    not already boosted. Sentence-initial capitals are ignored since every
    sentence starts capitalised; that's why we split on sentence
    punctuation first. Returns ``[{term, count}]`` ranked by frequency.
    """
    have = {t.strip().lower() for t in existing_boost}
    counts: Counter = Counter()

    for transcript in transcripts:
        for sentence in _SENTENCE_SPLIT_RE.split(transcript):
            tokens = _WORD_RE.findall(sentence)
            run: List[str] = []
            for idx, tok in enumerate(tokens):
                capitalised = tok[:1].isupper()
                # idx == 0 is sentence-initial → its capital is not signal.
                if capitalised and idx > 0 and _is_candidate_token(tok):
                    run.append(tok)
                    continue
                if run:
                    _emit_run(run, counts)
                    run = []
            if run:
                _emit_run(run, counts)

    candidates = [
        {"term": term, "count": n}
        for term, n in counts.most_common()
        if n >= min_count and term.lower() not in have
    ]
    return candidates


def _emit_run(run: List[str], counts: Counter) -> None:
    """Record a capitalised run as both the full phrase and its head token.

    "Claude Code" counts as the phrase *and* "Claude"/"Code" individually,
    so multi-word jargon and standalone proper nouns both surface.
    """
    if len(run) > 1:
        counts[" ".join(run)] += 1
    for tok in run:
        counts[tok] += 1


# ------------------------------------------------------------- LLM clustering


async def llm_cluster_replacements(
    terms: List[str],
    transcripts: List[str],
    existing_boost: List[str],
    *,
    hub_base_url: str,
    model: str,
) -> List[Dict[str, str]]:
    """Ask the hub itself to cluster terms with their common mis-hearings.

    Sends the candidate terms + a sample of context to the hub's
    Anthropic-shaped endpoint and asks for ``{canonical, variants[]}``
    clusters, which become ``{from: variant, to: canonical}`` replacement
    candidates. Best-effort: any transport/parse failure returns ``[]`` so
    the heuristic suggestions still come back.
    """
    if not terms:
        return []
    vocab = sorted({*terms, *existing_boost})
    sample = "\n".join(transcripts)[:6000]
    prompt = (
        "You are curating a speech-to-text correction dictionary. Below is "
        "a list of known/likely-correct domain terms, then a sample of raw "
        "dictation transcripts that may contain mis-transcriptions of those "
        "terms.\n\nKnown terms:\n"
        + ", ".join(vocab)
        + "\n\nTranscript sample:\n\"\"\"\n"
        + sample
        + "\n\"\"\"\n\nReturn ONLY a JSON array of objects "
        '{"canonical": "<correct term>", "variants": ["<mis-transcription>", ...]} '
        "for terms that actually appear mis-spelled in the sample. Variants "
        "must be strings that literally occur in the sample and differ from "
        "the canonical term. If you find none, return []."
    )
    payload = {
        "model": model,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    }
    base = hub_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{base}/v1/messages", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        logger.info("ℹ️ miner LLM pass skipped (hub call failed: %s)", exc)
        return []

    text = _extract_anthropic_text(data)
    clusters = _parse_clusters(text)
    return _clusters_to_replacements(clusters, transcripts)


def _extract_anthropic_text(data: Any) -> str:
    """Pull the text out of an Anthropic-shaped ``/v1/messages`` response."""
    if not isinstance(data, dict):
        return ""
    parts = data.get("content")
    if isinstance(parts, list):
        return "".join(
            p.get("text", "") for p in parts if isinstance(p, dict)
        )
    return ""


def _parse_clusters(text: str) -> List[Dict[str, Any]]:
    """Parse the model's JSON array, tolerating prose/code-fence wrapping."""
    if not text:
        return []
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _clusters_to_replacements(
    clusters: List[Dict[str, Any]], transcripts: List[str]
) -> List[Dict[str, str]]:
    """Turn ``{canonical, variants}`` clusters into ``{from, to}`` rules.

    Keeps only variants that literally occur in the corpus and differ from
    the canonical term, de-duped on the source phrase.
    """
    corpus = "\n".join(transcripts).lower()
    seen = set()
    out: List[Dict[str, str]] = []
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        canonical = str(cluster.get("canonical", "")).strip()
        variants = cluster.get("variants", [])
        if not canonical or not isinstance(variants, list):
            continue
        for v in variants:
            src = str(v).strip()
            key = src.lower()
            if (
                src
                and key != canonical.lower()
                and key not in seen
                and key in corpus
            ):
                seen.add(key)
                out.append({"from": src, "to": canonical})
    return out


# ------------------------------------------------------------- orchestration


async def mine_suggestions(days: Optional[int] = None) -> Dict[str, Any]:
    """Mine the corpus and return reviewable suggestions (never writes).

    Shape: ``{boost_terms: [{term, count}], replacements: [{from, to}],
    meta: {...}}``. ``days`` overrides the configured default.
    """
    cfg = load_miner_config()
    window = int(days) if days is not None else int(cfg["default_days"])
    if window < 1:
        window = int(cfg["default_days"])

    transcripts, vt_sha = await fetch_transcripts(
        cfg["voice_transcriber_base_url"], window, int(cfg["max_tokens"])
    )

    glossary = load_glossary()
    existing_boost = [t for t in glossary.get("boost_terms", []) if isinstance(t, str)]

    boost_candidates = heuristic_candidates(
        transcripts, existing_boost, min_count=int(cfg["min_count"])
    )

    replacements: List[Dict[str, str]] = []
    llm_used = False
    if cfg.get("use_llm") and boost_candidates:
        from src.host_profile import hub_port

        hub_url = f"http://127.0.0.1:{hub_port()}"
        replacements = await llm_cluster_replacements(
            [c["term"] for c in boost_candidates[:40]],
            transcripts,
            existing_boost,
            hub_base_url=hub_url,
            model=str(cfg["llm_model"]),
        )
        llm_used = True

    # Don't propose a replacement whose target collides with an existing rule.
    have_repl = {
        str(r.get("from", "")).strip().lower()
        for r in glossary.get("replacements", [])
        if isinstance(r, dict)
    }
    replacements = [r for r in replacements if r["from"].lower() not in have_repl]

    return {
        "boost_terms": boost_candidates,
        "replacements": replacements,
        "meta": {
            "days": window,
            "n_sessions": len(transcripts),
            "vt_git_sha": vt_sha,
            "llm_used": llm_used,
        },
    }
