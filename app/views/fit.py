"""Fit estimator — paste a Hugging Face URL, see if it'll run here."""

from __future__ import annotations

import streamlit as st

from src import fit_estimator as fe
from src import machine_specs as ms


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_fetch(repo_id: str) -> fe.HFModelInfo:
    return fe.fetch_hf_info(repo_id)


def _machine_panel(specs: ms.MachineSpecs | None) -> None:
    if specs is None:
        st.warning(
            "`config/machine_specs.yaml` not found. Run "
            "`python scripts/detect_machine_specs.py` to populate it. "
            "Without specs the verdict is informational only."
        )
        return

    primary = specs.primary_gpu
    vram = primary.vram_gb if primary else 0.0
    cols = st.columns(4)
    cols[0].metric("Machine", specs.name)
    cols[1].metric("Primary GPU", primary.name if primary else "—",
                   f"{vram:.0f} GB VRAM" if primary else "")
    cols[2].metric("System RAM", f"{specs.ram_gb:.0f} GB")
    cols[3].metric("CPU", specs.cpu_model.replace("AMD ", "").replace("Intel(R) ", ""),
                   f"{specs.cpu_cores}c / {specs.cpu_threads}t")


def _verdict_callout(estimate: fe.FitEstimate) -> None:
    if estimate.verdict == "fits_gpu":
        st.success(f"✅ {estimate.summary}")
    elif estimate.verdict == "fits_offload":
        st.warning(f"🟡 {estimate.summary}")
    elif estimate.verdict == "no_fit":
        st.error(f"🔴 {estimate.summary}")
    else:
        st.info(estimate.summary)


def _shape_table(shape: fe.ModelShape) -> None:
    rows = [
        ("Total params", f"{shape.total_params_b:.1f} B"),
        ("Active params", f"{shape.active_params_b:.1f} B"
            if shape.active_params_b else "— (dense)"),
        ("Architecture", shape.architecture or "—"),
        ("MoE", "yes" if shape.is_moe else "no"),
        ("Experts", str(shape.num_experts) if shape.num_experts else "—"),
        ("Layers", str(shape.num_layers) if shape.num_layers else "—"),
        ("KV heads", str(shape.num_kv_heads) if shape.num_kv_heads else "—"),
        ("Head dim", str(shape.head_dim) if shape.head_dim else "—"),
        ("Max position", f"{shape.max_position_embeddings:,}"
            if shape.max_position_embeddings else "—"),
    ]
    if shape.weight_dtype_summary:
        dtypes = ", ".join(f"{k}: {v / 1e9:.1f} B"
                           for k, v in sorted(
                               shape.weight_dtype_summary.items(),
                               key=lambda kv: -kv[1]))
        rows.append(("Safetensors dtypes", dtypes))
    st.dataframe(
        {"field": [r[0] for r in rows], "value": [r[1] for r in rows]},
        hide_index=True, width="stretch",
    )


def _footprint_table(estimate: fe.FitEstimate) -> None:
    rows = [
        ("Weights", f"{estimate.weights_gb:.2f} GB"),
        ("KV cache", f"{estimate.kv_cache_gb:.2f} GB"),
        ("Overhead", f"{estimate.overhead_gb:.2f} GB"),
        ("**Total**", f"**{estimate.total_gb:.2f} GB**"),
    ]
    st.dataframe(
        {"component": [r[0] for r in rows], "size": [r[1] for r in rows]},
        hide_index=True, width="stretch",
    )


def render() -> None:
    st.title("🧮 Fit estimator")
    st.caption(
        "Paste a Hugging Face model URL. We pull the model card metadata, "
        "estimate the memory footprint at a chosen precision and context "
        "length, and check it against this machine's specs."
    )

    specs = ms.load()
    _machine_panel(specs)
    st.divider()

    with st.form(key="fit_form", clear_on_submit=False):
        url = st.text_input(
            "Hugging Face model URL or `owner/name`",
            key="fit_url_input",
            placeholder="https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash",
        )
        submitted = st.form_submit_button("Analyze", type="primary",
                                          width="content")

    if not submitted and not url:
        st.info(
            "Try something like "
            "`https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash` or "
            "`unsloth/Qwen3.5-9B-GGUF`."
        )
        return

    repo_id = fe.parse_repo_id(url)
    if not repo_id:
        st.error("Could not parse a `owner/name` repo id from that input.")
        return

    with st.spinner(f"Fetching `{repo_id}` from Hugging Face…"):
        info = _cached_fetch(repo_id)

    if info.error:
        st.error(info.error)
        return

    if not info.shape or info.shape.total_params_b == 0:
        st.warning(
            f"Could not derive parameter count for `{repo_id}` from the model "
            f"card. The repo may be GGUF-only with no `config.json`, gated, "
            f"or use an unrecognised architecture."
        )
        if info.file_total_bytes:
            st.caption(
                f"Sum of weight files on the repo: "
                f"{info.file_total_bytes / (1024 ** 3):.1f} GB."
            )
        return

    shape = info.shape
    default_quant, quant_reason = fe.suggest_default_quant(info)
    default_ctx = min(shape.max_position_embeddings or 8192, 32768)

    left, right = st.columns([1, 1])

    with left:
        st.subheader(f"`{repo_id}`")
        if info.pipeline_tag:
            st.caption(f"Pipeline: {info.pipeline_tag}")
        _shape_table(shape)

    with right:
        st.subheader("Knobs")
        try:
            quant_index = fe.QUANT_CHOICES.index(default_quant)
        except ValueError:
            quant_index = fe.QUANT_CHOICES.index("Q4_K_M")
        quant = st.selectbox(
            "Target precision",
            fe.QUANT_CHOICES,
            index=quant_index,
            key="fit_quant",
            help=f"Default {default_quant} ({quant_reason}).",
        )
        ctx = st.slider(
            "Context length (tokens)",
            min_value=512,
            max_value=int(shape.max_position_embeddings or 131072),
            value=int(default_ctx),
            step=512,
            key="fit_context",
        )
        kv_dtype = st.radio(
            "KV cache precision",
            options=["FP16 (2 B)", "FP8 (1 B)"],
            horizontal=True,
            key="fit_kv_dtype",
        )
        kv_bytes = 2 if kv_dtype.startswith("FP16") else 1

    primary = specs.primary_gpu if specs else None
    vram = primary.vram_gb if primary else None
    ram = specs.ram_gb if specs else None

    estimate = fe.estimate_fit(shape, quant, ctx, vram, ram, kv_bytes=kv_bytes)

    st.divider()
    _verdict_callout(estimate)
    _footprint_table(estimate)
    for note in estimate.notes:
        st.caption(f"• {note}")

    with st.expander("How this is calculated", expanded=False):
        st.markdown(
            "- **Weights** = total params × bytes-per-parameter for the "
            "chosen precision (GGUF k-quants use empirical avg bits/weight).\n"
            "- **KV cache** = 2 × layers × KV heads × head dim × context × "
            "KV bytes (single sequence, batch=1).\n"
            "- **Overhead** = fixed 1 GB for activations + framework state.\n"
            "- **Fits on GPU** if total ≤ VRAM × "
            f"{int(fe.GPU_HEADROOM * 100)}%.\n"
            "- **Fits with offload** if total ≤ (VRAM + RAM) × "
            f"{int(fe.TOTAL_HEADROOM * 100)}%.\n"
            "- MoE: full expert weights are still resident; active-param "
            "count only reduces compute per token.\n"
        )
