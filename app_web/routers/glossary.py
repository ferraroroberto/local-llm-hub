"""Glossary tab API — view/edit the transcription dictionary + mine suggestions.

Backs the inline dictionary editor that opens from a whisper row on the
Models tab (issue #94). The dictionary itself
(``config/transcription_glossary.json``) is owned by
``src.transcription_glossary``; this router is a thin CRUD + mining shell
over it:

  * ``GET  /api/glossary``       → the editable ``{replacements, boost_terms}``.
  * ``PUT  /api/glossary``       → validate + persist, invalidating the
    replacement-rule cache so edits apply without a hub restart.
  * ``POST /api/glossary/mine``  → run the transcript miner (reads
    voice-transcriber's session API) and return reviewable suggestions —
    never writes.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from src import transcription_glossary as tg

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/glossary")
async def get_glossary() -> Dict[str, Any]:
    """Return the editable dictionary shape for the admin editor."""
    return tg.load_glossary()


@router.put("/api/glossary")
async def put_glossary(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Persist an edited dictionary.

    Writes ``config/transcription_glossary.json`` atomically and clears the
    replacement-rule cache. ``boost_terms`` changes only bind on the next
    whisper launch (boosting is a launch-time arg) — surfaced to the user
    via ``boost_terms_need_restart``.
    """
    try:
        saved = tg.save_glossary(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "ok": True,
        "glossary": saved,
        # Replacement edits are live now; boost-term edits need a relaunch.
        "boost_terms_need_restart": True,
    }


@router.post("/api/glossary/mine")
async def mine_glossary(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Mine recent transcripts for candidate boost terms + replacements.

    Reads the last *N* days from voice-transcriber's session API over
    loopback and returns reviewable suggestions; it never writes the
    dictionary. ``days`` may override the configured default.
    """
    from src.dictionary_miner import mine_suggestions, MinerError

    days = None
    if isinstance(payload, dict) and payload.get("days") is not None:
        try:
            days = int(payload["days"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="'days' must be an integer")

    try:
        return await mine_suggestions(days=days)
    except MinerError as exc:
        # The corpus app being down / unreachable is an expected, recoverable
        # condition — surface it as 502 with a readable message, not a 500.
        raise HTTPException(status_code=502, detail=str(exc))
