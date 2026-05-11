"""Playground — send a prompt to the local API, show reply + token counts."""

from __future__ import annotations

import base64
import time
from typing import Optional

import httpx
import streamlit as st

from src import server_process as sp
from src.model_registry import Model, enabled_models, resolve as resolve_model

# Backends that support image content blocks via the hub.
_IMAGE_BACKENDS = {"claude", "gemini"}

# Media-type lookup for the image-block payload. Keep aligned with the
# server-side `_extract_image_blocks` extension map.
_MEDIA_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}


def _model_options() -> list[str]:
    """Aliases-only, sorted A-Z.

    Only models that have at least one alias appear — aliases are the
    stable identifiers (e.g. `claude_haiku`, `gemini_pro`,
    `agentic_light`) that survive version bumps, so they're the right
    thing for callers to address. Models without aliases (e.g.
    standalone whisper rows) are intentionally hidden from the
    playground; address them by display_name from code if needed.
    """
    aliases: list[str] = []
    for m in enabled_models():
        aliases.extend(m.aliases)
    return sorted(dict.fromkeys(aliases))


def render() -> None:
    st.title("💬 Playground")
    st.caption(f"POSTs to `{sp.BASE_URL}/v1/messages`")

    reachable = sp.is_reachable()
    if not reachable:
        st.warning(
            "Server unreachable. Start it in the **🛰 Server** tab, then "
            "come back here."
        )

    cols = st.columns([1, 2])
    with cols[0]:
        options = _model_options()
        model = st.selectbox("Model", options, index=0) if options else None
        apply_max = st.checkbox(
            "Apply max_tokens",
            value=False,
            help=(
                "When off, no cap is sent — the backend decides when to stop "
                "(`claude -p` / `gemini -p` for subscription routes; "
                "llama-server for local backends)."
            ),
        )
        max_tokens = st.number_input(
            "max_tokens",
            min_value=16,
            max_value=8192,
            value=512,
            step=16,
            disabled=not apply_max,
        )
    with cols[1]:
        system = st.text_area(
            "System prompt (optional)",
            value="",
            height=100,
            placeholder="e.g. Answer in one sentence.",
        )

    prompt = st.text_area(
        "Your message",
        height=160,
        placeholder="Type a prompt and press Send…",
        key="pg_prompt",
    )

    # Image uploader appears only for backends that actually accept images
    # through the hub (claude-*, gemini-*). Local llama.cpp backends are
    # text-only — the hub would return 400 — so the widget is hidden to
    # keep the testing surface honest.
    resolved: Optional[Model] = resolve_model(model) if model else None
    images_enabled = resolved is not None and resolved.backend in _IMAGE_BACKENDS
    uploads = []
    if images_enabled:
        uploads = st.file_uploader(
            "Images (optional)",
            type=list(_MEDIA_TYPES.keys()),
            accept_multiple_files=True,
            key=f"pg_images_{st.session_state.get('pg_uploader_nonce', 0)}",
            help=(
                "Attach one or more images to test multimodal input. "
                "Sent as Anthropic-style `image` content blocks; the hub "
                "writes them to a per-request temp dir before invoking "
                "the CLI."
            ),
        ) or []
        if uploads:
            # Render small fixed-width thumbnails inline — the uploader
            # itself already lists filenames, so the preview just needs
            # to confirm "yes, that's the right image" at a glance.
            for up in uploads:
                st.image(up.getvalue(), caption=up.name, width=96)

    send = st.button(
        "Send",
        type="primary",
        disabled=not reachable or not prompt.strip() or not model,
    )

    if send:
        # Build the user message. With no images we send a plain string
        # (preserves the text-only path for local backends); with images
        # we switch to Anthropic-shaped content blocks the hub knows how
        # to route into claude_cli / gemini_cli.
        if uploads:
            blocks: list[dict] = [{"type": "text", "text": prompt}]
            for up in uploads:
                ext = (up.name.rsplit(".", 1)[-1] or "").lower()
                media_type = _MEDIA_TYPES.get(ext, "image/png")
                blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64.b64encode(up.getvalue()).decode("ascii"),
                    },
                })
            user_content: object = blocks
        else:
            user_content = prompt

        payload: dict = {
            "model": model,
            "messages": [{"role": "user", "content": user_content}],
        }
        if apply_max:
            payload["max_tokens"] = int(max_tokens)
        if system.strip():
            payload["system"] = system

        t0 = time.time()
        try:
            with st.spinner(f"calling {model} via hub …"):
                r = httpx.post(
                    f"{sp.BASE_URL}/v1/messages",
                    json=payload,
                    timeout=300.0,
                )
        except Exception as e:
            st.error(f"request failed: {e}")
            return

        elapsed = time.time() - t0

        # Bump the uploader's key so attached images clear after a
        # successful round-trip. We don't clear on HTTP errors so the
        # user can retry without re-attaching.
        if r.status_code == 200 and uploads:
            st.session_state["pg_uploader_nonce"] = (
                st.session_state.get("pg_uploader_nonce", 0) + 1
            )

        if r.status_code != 200:
            st.error(f"HTTP {r.status_code}")
            st.code(r.text, language="json")
            return

        body = r.json()
        text = body["content"][0]["text"] if body.get("content") else ""
        usage = body.get("usage", {})

        in_new = int(usage.get("input_tokens", 0) or 0)
        cache_r = int(usage.get("cache_read_input_tokens", 0) or 0)
        cache_w = int(usage.get("cache_creation_input_tokens", 0) or 0)
        in_total = in_new + cache_r + cache_w

        metrics = st.columns(6)
        metrics[0].metric("input (total)", in_total)
        metrics[1].metric("input (new)", in_new)
        metrics[2].metric("cache read", cache_r)
        metrics[3].metric("cache write", cache_w)
        metrics[4].metric("output tokens", usage.get("output_tokens", 0))
        metrics[5].metric("elapsed", f"{elapsed:.1f}s")
        if cache_r or cache_w:
            st.caption(
                "`input (total) = input (new) + cache read + cache write`. "
                "Claude Code caches the system preamble, tools, and often "
                "your message itself, so `input (new)` is usually tiny — "
                "the real prompt size is the total."
            )
        elif in_total == 0 and int(usage.get("output_tokens", 0) or 0) == 0:
            st.caption(
                "Token counts are zero — the `gemini -p` CLI doesn't "
                "surface usage data, so the hub reports zeros on the "
                "`gemini-*` path."
            )

        st.markdown("**Response**")
        st.markdown(text or "_(empty response)_")

        with st.expander("Raw JSON response"):
            st.json(body)
