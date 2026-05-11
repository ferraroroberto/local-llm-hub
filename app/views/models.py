"""Per-backend start/stop/health/log panel for each enabled model."""

from __future__ import annotations

import streamlit as st

from src import backend_process as lp
from src.model_registry import enabled_models


def _render_claude_card(m) -> None:
    st.subheader(f"🌀 {m.display_name}")
    st.caption(
        "Anthropic subscription via `claude -p`. Lives inside the hub "
        "process; controlled from the 🛰 Server tab. Routed to the CLI "
        f"as `--model {m.display_name}`."
    )
    if m.aliases:
        st.caption("Aliases: " + ", ".join(m.aliases))


def _render_gemini_card(m) -> None:
    st.subheader(f"♊ {m.display_name}")
    st.caption(
        "Google AI Pro subscription via `gemini -p`. Lives inside the hub "
        "process; AI Pro/Ultra raises daily quotas, shared with Gemini Code "
        "Assist. Set up once with `gemini /auth login`."
    )
    if m.aliases:
        st.caption("Aliases: " + ", ".join(m.aliases))


def _render_local_card(m) -> None:
    own = lp.ownership(m.id)
    is_ours = own == lp.OWNERSHIP_OURS
    is_external = own == lp.OWNERSHIP_EXTERNAL
    reachable = lp.is_reachable(m) if (is_ours or is_external) else False
    ext_pid = lp.external_pid(m.id)

    is_whisper = m.backend == "whisper" or m.engine == "whisper-server"
    glyph = "🎙" if is_whisper else "🦙"
    engine_label = "whisper-server" if is_whisper else "llama-server"
    st.subheader(f"{glyph} {m.display_name}")
    st.caption(f"`{engine_label}` on :{m.port} — id `{m.id}`")

    process_label = (
        "running (managed)" if is_ours
        else "running (external)" if is_external
        else "stopped"
    )
    pid_label = (
        str(lp.pid(m.id)) if is_ours
        else (str(ext_pid) if ext_pid else "—") if is_external
        else "—"
    )

    cols = st.columns(4)
    cols[0].metric("Process", process_label)
    cols[1].metric("PID", pid_label)
    cols[2].metric("Health", "ok" if reachable else "—")
    cols[3].metric("Log lines", f"{len(lp.log_lines(m.id))}")

    ctrl = st.columns([1, 1, 1, 4])
    with ctrl[0]:
        if st.button("▶ Start", key=f"start_{m.id}", type="primary",
                     disabled=(own != lp.OWNERSHIP_NONE), width="stretch"):
            ok, msg = lp.start(m.id)
            (st.success if ok else st.warning)(msg)
            st.rerun()
    with ctrl[1]:
        if is_external:
            label = f"💀 Stop external (PID {ext_pid})" if ext_pid else "💀 Stop external"
            if st.button(label, key=f"force_stop_{m.id}", width="stretch"):
                ok, msg = lp.force_stop_external(m.id)
                (st.success if ok else st.error)(msg)
                st.rerun()
        else:
            if st.button("■ Stop", key=f"stop_{m.id}",
                         disabled=not is_ours, width="stretch"):
                ok, msg = lp.stop(m.id)
                (st.success if ok else st.warning)(msg)
                st.rerun()
    with ctrl[2]:
        if st.button("🔄 Refresh", key=f"refresh_{m.id}", width="stretch"):
            st.rerun()

    if is_external:
        st.info(
            f"Adopted — port :{m.port} is held by another process"
            + (f" (PID {ext_pid})" if ext_pid else "")
            + ". This session didn't spawn it; logs aren't available here."
        )

    with st.expander("Log tail", expanded=False):
        if is_external:
            st.code("(adopted — no log tail available)", language="log")
        else:
            lines = lp.log_lines(m.id)
            body = "\n".join(lines[-400:]) if lines else "(no output yet — start the backend)"
            st.code(body, language="log")

    with st.expander("Launch args", expanded=False):
        try:
            cmd = lp.build_command(m)
            st.code(" ".join(cmd), language="bash")
        except Exception as e:
            st.warning(str(e))


def render() -> None:
    st.title("🧠 Models")
    st.caption(
        "One card per model enabled for this host. Local `llama-server` "
        "backends can be started/stopped individually; the hub on :8000 "
        "routes requests by `model` name."
    )

    models = enabled_models()
    if not models:
        st.warning("No models enabled for this host — check `config/models.yaml`.")
        return

    for m in models:
        if m.backend == "claude":
            _render_claude_card(m)
        elif m.backend == "gemini":
            _render_gemini_card(m)
        elif m.backend in ("openai", "whisper"):
            _render_local_card(m)
        else:
            st.write(f"unknown backend {m.backend!r} for {m.display_name}")
        st.divider()
