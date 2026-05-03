"""Server control page — start / stop the FastAPI process + tail its log."""

from __future__ import annotations

import streamlit as st

from src import server_process as sp
from src import system_stats as stats


def render() -> None:
    st.title("🛰 Server")

    lan = sp.lan_url()
    local_md = f"[{sp.BASE_URL}]({sp.BASE_URL})"
    if lan:
        st.markdown(f"**Local:** {local_md}  \n**LAN:** [{lan}]({lan})")
        st.caption(
            "The server binds on 0.0.0.0, so other machines on the LAN can "
            "reach it at the LAN URL. On Windows, allow the Python process "
            "through the firewall on the first run."
        )
    else:
        st.markdown(f"**Local:** {local_md}")
        st.caption("No LAN route detected — reachable from this machine only.")

    own = sp.ownership()
    is_ours = own == sp.OWNERSHIP_OURS
    is_external = own == sp.OWNERSHIP_EXTERNAL
    reachable = sp.is_reachable() if (is_ours or is_external) else False
    ext_pid = sp.external_pid()
    process_label = (
        "running (managed)" if is_ours
        else "running (external)" if is_external
        else "stopped"
    )
    pid_label = (
        str(sp.pid()) if is_ours
        else (str(ext_pid) if ext_pid else "—") if is_external
        else "—"
    )

    cols = st.columns(4)
    cols[0].metric("Process", process_label)
    cols[1].metric("PID", pid_label)
    cols[2].metric("Health", "ok" if reachable else "—")
    cols[3].metric("Log lines", f"{len(sp.log_lines())}")

    _render_resource_bars()

    ctrl = st.columns([1, 1, 1, 4])
    with ctrl[0]:
        # Disabled when anything (us or external) holds the port.
        if st.button("▶ Start", type="primary", disabled=(own != sp.OWNERSHIP_NONE), width="stretch"):
            ok, msg = sp.start()
            (st.success if ok else st.warning)(msg)
            st.rerun()
    with ctrl[1]:
        if is_external:
            label = f"💀 Stop external (PID {ext_pid})" if ext_pid else "💀 Stop external"
            if st.button(label, width="stretch"):
                ok, msg = sp.force_stop_external()
                (st.success if ok else st.error)(msg)
                st.rerun()
        else:
            if st.button("■ Stop", disabled=not is_ours, width="stretch"):
                ok, msg = sp.stop()
                (st.success if ok else st.warning)(msg)
                st.rerun()
    with ctrl[2]:
        if st.button("🔄 Refresh", width="stretch"):
            st.rerun()

    if is_external:
        st.info(
            f"Hub on :{sp.PORT} is **adopted** — held by another process "
            f"(PID {ext_pid}), most likely the tray or a `run_hub` launcher. "
            "It's reachable and routing requests normally; this session just "
            "didn't spawn it. Use **Stop external** to reclaim the port if "
            "you want to take over."
        )

    st.divider()

    st.markdown("**Server log** (stdout + stderr)")
    if is_external:
        st.caption(
            "Log tail is unavailable for adopted processes — Windows can't "
            "attach to another process's stdout post-hoc. See the launcher "
            "that owns the hub for its output."
        )
        st.code("(adopted — no log tail available)", language="log")
    else:
        lines = sp.log_lines()
        body = "\n".join(lines[-400:]) if lines else "(no output yet — start the server)"
        st.code(body, language="log")

    st.caption(
        "The process is managed by this Streamlit session unless adopted. "
        "Stopping the app stops only servers it spawned. For standalone use, "
        "run `run_hub.bat` (or `tray.bat` on Windows for a silent system-tray "
        "launcher)."
    )


@st.fragment(run_every="5s")
def _render_resource_bars() -> None:
    ram = stats.ram_stats()
    gpus = stats.gpu_stats()

    st.caption("**System resources** (auto-refresh 5s)")

    bars = 1 + 2 * len(gpus)
    cols = st.columns(bars)

    with cols[0]:
        st.progress(min(ram["percent"] / 100.0, 1.0))
        st.caption(
            f"RAM · {ram['used_gb']:.1f} / {ram['total_gb']:.1f} GB · "
            f"{ram['percent']:.0f}%"
        )

    for idx, gpu in enumerate(gpus):
        short = _short_gpu_name(gpu.get("name") or f"GPU {idx}")
        vram_pct = gpu.get("vram_percent")
        used_mb = gpu.get("used_mb")
        total_mb = gpu.get("total_mb")
        util_pct = gpu.get("util_percent")

        with cols[1 + 2 * idx]:
            value = (vram_pct or 0.0) / 100.0
            st.progress(min(value, 1.0))
            if used_mb is not None and total_mb is not None:
                st.caption(
                    f"VRAM · {short} · {used_mb / 1024:.1f} / "
                    f"{total_mb / 1024:.1f} GB · {vram_pct:.0f}%"
                )
            else:
                st.caption(f"VRAM · {short} · n/a")

        with cols[2 + 2 * idx]:
            value = (util_pct or 0.0) / 100.0
            st.progress(min(value, 1.0))
            if util_pct is not None:
                st.caption(f"GPU util · {short} · {util_pct:.0f}%")
            else:
                st.caption(f"GPU util · {short} · n/a")


def _short_gpu_name(name: str) -> str:
    cleaned = name.replace("NVIDIA ", "").replace("GeForce ", "").strip()
    return cleaned or name
