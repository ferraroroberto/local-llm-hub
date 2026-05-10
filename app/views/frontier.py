"""Frontier tab — read-only view of the monthly research run.

Three sections, top to bottom:

  1. Run picker         — dropdown of ``docs/frontier/runs/<date>/``
  2. Current decisions  — read-only summary of ``roles:`` from models.yaml
  3. Report + chart     — markdown of ``report.md`` and embedded ``frontier.html``

This tab is *display only*. To produce a new run, invoke
``/frontier-refresh`` from Claude Code. To swap a role to a different
model, invoke ``/swap-model``. Both commands edit files directly with
human-in-the-loop confirmation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import streamlit as st
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUNS_DIR = PROJECT_ROOT / "docs" / "frontier" / "runs"
LATEST_FILE = RUNS_DIR / "LATEST"
MODELS_YAML = PROJECT_ROOT / "config" / "models.yaml"

ROLE_LABELS = {
    "agentic_light": "Agentic — light",
    "agentic_heavy": "Agentic — heavy",
    "audio_transcribe": "Audio — transcribe",
    "audio_translate": "Audio — translate (lazy)",
}


# ---------- helpers ----------

@st.cache_data(ttl=60)
def _list_runs() -> List[str]:
    if not RUNS_DIR.exists():
        return []
    return sorted(
        (p.name for p in RUNS_DIR.iterdir() if p.is_dir()),
        reverse=True,
    )


def _latest_run() -> Optional[str]:
    if LATEST_FILE.exists():
        s = LATEST_FILE.read_text(encoding="utf-8").strip()
        if s and (RUNS_DIR / s).is_dir():
            return s
    runs = _list_runs()
    return runs[0] if runs else None


def _read_run_text(run: str, name: str) -> str:
    p = RUNS_DIR / run / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _read_roles() -> Dict[str, Optional[str]]:
    """Return role -> model display_name (falling back to model_id)."""
    if not MODELS_YAML.exists():
        return {}
    cfg = yaml.safe_load(MODELS_YAML.read_text(encoding="utf-8")) or {}
    roles = cfg.get("roles") or {}
    models = cfg.get("models") or {}

    def _display(model_id: Optional[str]) -> Optional[str]:
        if not model_id:
            return None
        entry = models.get(model_id) or {}
        return entry.get("display_name") or model_id

    audio = roles.get("audio") or {}
    return {
        "agentic_light": _display((roles.get("agentic_light") or {}).get("model_id")),
        "agentic_heavy": _display((roles.get("agentic_heavy") or {}).get("model_id")),
        "audio_transcribe": _display((audio.get("transcribe") or {}).get("model_id")),
        "audio_translate": _display((audio.get("translate") or {}).get("model_id")),
    }


# ---------- sections ----------

def _section_run_picker() -> Optional[str]:
    runs = _list_runs()
    if not runs:
        st.warning(
            "No frontier runs yet. Run `/frontier-refresh` from Claude Code "
            "to produce one."
        )
        return None
    default = _latest_run() or runs[0]
    default_idx = runs.index(default) if default in runs else 0
    return st.selectbox(
        "Run",
        runs,
        index=default_idx,
        key="frontier_selected_run",
        help="Each run is a snapshot under docs/frontier/runs/<date>/",
    )


def _section_current_decisions() -> None:
    """Read-only display of the current `roles:` mapping in models.yaml."""
    st.subheader("🛠 Current role decisions")
    st.caption(
        "Read from `config/models.yaml` → `roles:`. To change a row, run "
        "`/swap-model` in Claude Code — that command reads the latest run, "
        "asks what to swap, and edits everything (yaml, launchers, weights)."
    )
    roles = _read_roles()
    cols = st.columns(4)
    for i, role in enumerate(("agentic_light", "agentic_heavy",
                              "audio_transcribe", "audio_translate")):
        value = roles.get(role) or "—"
        cols[i].markdown(
            f"<div style='color:rgba(250,250,250,0.6);font-size:0.85rem;'>"
            f"{ROLE_LABELS[role]}</div>"
            f"<div style='font-size:1.1rem;font-weight:600;"
            f"word-break:break-word;line-height:1.3;margin-top:0.25rem;'>"
            f"{value}</div>",
            unsafe_allow_html=True,
        )


def _section_report(run: str) -> None:
    text = _read_run_text(run, "report.md")
    if not text:
        st.info("This run has no `report.md`.")
        return
    with st.expander("📄 Report (markdown)", expanded=True):
        st.markdown(text, unsafe_allow_html=False)


def _section_chart(run: str) -> None:
    html = _read_run_text(run, "frontier.html")
    if not html:
        st.info("This run has no `frontier.html`.")
        return
    with st.expander("📊 Chart (interactive)", expanded=True):
        st.components.v1.html(html, height=900, scrolling=True)


# ---------- entry point ----------

def render() -> None:
    st.title("🛰 Frontier")
    st.caption(
        "Monthly local-AI frontier snapshot. Read-only here — use "
        "`/frontier-refresh` to produce a new run and `/swap-model` to "
        "act on it. The `claude` subscription path is not part of any "
        "role and is unaffected."
    )
    selected = _section_run_picker()
    if not selected:
        return
    st.divider()
    _section_current_decisions()
    st.divider()
    _section_report(selected)
    _section_chart(selected)
