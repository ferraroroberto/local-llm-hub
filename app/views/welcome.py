"""Welcome page — introduction + usage instructions."""

from __future__ import annotations

import streamlit as st


def render() -> None:
    st.title("👋 Local LLM Hub")
    st.caption(
        "A local Anthropic- and OpenAI-shaped API backed by `claude -p`, "
        "`gemini -p`, local llama-server backends, and a whisper.cpp ASR pair."
    )

    st.markdown(
        """
        ### What this is

        A tiny FastAPI server that exposes `POST /v1/messages`
        (Anthropic shape) and `POST /v1/chat/completions` (OpenAI shape)
        on `http://127.0.0.1:8000`, routing each call to the right
        backend by the `model` field. Same wire shape as the official
        Anthropic / OpenAI APIs, so the official `anthropic` and
        `openai` SDKs work unchanged.

        - `claude-*` models go through your local `claude -p` CLI and
          your Claude Code subscription — no API key needed.
        - `gemini-*` models go through your local `gemini -p` CLI and
          your Google AI Pro subscription (browser sign-in) — no API
          key needed. Falls back to `GEMINI_API_KEY` if set.
        - Local `qwen3.5-4b` and `gemma4-26b-a4b-it` go through
          `llama-server` on loopback ports.
        - `whisper-large-v3-turbo` (`:8090`) and `whisper-medium-translate`
          (`:8091`, eager CPU) run as separate `whisper-server` processes;
          audio clients hit them directly (the hub doesn't proxy audio).

        ### Active roles right now

        Four local roles, mapped in `config/models.yaml` → `roles:`:

        | Role | Model |
        |---|---|
        | `agentic_light` | `qwen3.5-4b` (OpenClaw fast lane / classify) |
        | `agentic_heavy` | `gemma4-26b-a4b-it` (deep agentic / transcripts / docs / ES↔EN↔CA) |
        | `audio_transcribe` | `whisper-large-v3-turbo` (EN/ES → text) |
        | `audio_translate` | `whisper-medium-translate` (eager CPU; ES → EN) |

        `gemma4-e4b-it`, `qwen3.5-9b`, and `glm-4.5-air` are kept as
        **ad-hoc candidates** — defined in `config/models.yaml` but not
        in the active rotation. Bring up via the matching
        `launchers/run_*.bat` if needed.

        ### Tabs in this app

        - **🛰 Server** — start / stop the FastAPI hub on `:8000` and watch its log.
        - **📊 Comparison** — per-model specs table (active rotation only).
        - **🧠 Models** — start / stop each enabled backend and tail its log.
        - **✅ Testing** — run unit tests and the end-to-end smoke test.
        - **💬 Playground** — send a prompt, pick a model, see the reply and token counts.
        - **🛰 Frontier** — read-only view of the latest monthly research run (report + chart) and the current role decisions.

        ### Subscription-backed cloud routes

        Two "always available" routes use your existing personal CLIs —
        no GCP project, no API keys, no per-call billing:

        | Model | Backend | Subscription |
        |---|---|---|
        | `claude_haiku` / `claude_sonnet` / `claude_opus` | `claude -p` | Anthropic Pro/Max |
        | `gemini-3.1-pro` (alias `gemini_pro`) | `gemini -p` | Google AI Pro (AI Pro required for 3.1 Pro since 2026-03-25) |
        | `gemini-3-flash` | `gemini -p` | Google AI Pro |
        | `gemini-3.1-flash-lite` (alias `gemini_lite`) | `gemini -p` | Google AI Pro |

        Image content blocks work on **both** subscription paths —
        attach a base64 image and the hub writes it to a per-request
        temp dir, then hands the file to `claude -p --add-dir` /
        `gemini -p @path`. Cleanup is automatic.

        ### Refreshing the roster (Claude Code slash commands)

        The monthly refresh and per-role swaps are driven from Claude
        Code, not from this UI:

        - **`/frontier-refresh`** — runs the research, regenerates
          `docs/frontier/runs/<today>/{report.md,frontier.json,frontier.html}`,
          repoints `LATEST`. Read-only on the registry.
        - **`/swap-model`** — interactive role swap. Reads the latest
          recommendations, asks one question at a time, shows the
          planned diff, edits `config/models.yaml` + writes a launcher
          + (optionally) downloads the new GGUF.

        See [`docs/frontier-via-slash-commands.md`](https://github.com/ferraroroberto/local-llm-hub/blob/main/docs/frontier-via-slash-commands.md)
        for why the swap path is a slash command and not a button in
        this app.

        ### Use it from your own code
        """
    )

    st.markdown("**Python (official Anthropic SDK):**")
    st.code(
        '''from anthropic import Anthropic

client = Anthropic(api_key="local-dummy", base_url="http://127.0.0.1:8000")

# Claude via subscription — alias survives version bumps
msg = client.messages.create(
    model="claude_haiku",   # alias for claude-haiku-4-5
    max_tokens=128,
    messages=[{"role": "user", "content": "Hello"}],
)
print(msg.content[0].text)

# Local agentic_light role — fast, full GPU
msg = client.messages.create(
    model="agentic_light",   # alias for qwen3.5-4b
    max_tokens=128,
    messages=[{"role": "user", "content": "Hello"}],
)
print(msg.content[0].text)

# Gemini 3.1 Pro via your Google AI Pro subscription
msg = client.messages.create(
    model="gemini_pro",   # alias for gemini-3.1-pro
    max_tokens=128,
    messages=[{"role": "user", "content": "Hello"}],
)
print(msg.content[0].text)
''',
        language="python",
    )

    st.markdown("**Raw HTTP (curl):**")
    st.code(
        'curl -s http://127.0.0.1:8000/v1/messages \\\n'
        '  -H "Content-Type: application/json" \\\n'
        '  -d \'{"model":"claude_haiku","max_tokens":64,'
        '"messages":[{"role":"user","content":"hi"}]}\'',
        language="bash",
    )

    st.markdown("**Audio (direct to whisper, hub doesn't proxy `/v1/audio/*`):**")
    st.code(
        '# Transcribe — turbo on :8090\n'
        'curl -s -F file=@clip.wav -F response_format=json \\\n'
        '  http://127.0.0.1:8090/v1/audio/transcriptions\n'
        '\n'
        '# Translate ES → EN — medium on :8091 (CPU, eager-loaded)\n'
        'curl -s -F file=@spanish.wav -F task=translate \\\n'
        '  http://127.0.0.1:8091/v1/audio/transcriptions',
        language="bash",
    )

    st.markdown(
        """
        ### LAN access

        The hub binds on `0.0.0.0:8000`, so other machines on your
        network (another laptop, a VM, an agent like openClaw) can call
        it directly. Open the **🛰 Server** tab to see the clickable
        **LAN** URL — point the remote client's `base_url` at it instead
        of `127.0.0.1`:

        ```python
        client = Anthropic(
            api_key="local-dummy",
            base_url="http://192.168.1.42:8000",   # your LAN IP here
        )
        ```

        On Windows the first run will prompt to allow Python through
        the firewall — accept on **Private** networks only. There is
        no authentication, so only run on trusted networks (home /
        office LAN you own) and never port-forward to the internet.

        ### Layout

        - **`app/`** — this Streamlit UI (entry point + views).
        - **`src/`** — non-UI Python: CLI wrapper, FastAPI server, process manager.
        - **`tests/`** — pytest unit tests.
        - **`scripts/`** — installer, downloader, end-to-end smoke test.
        - **`launchers/`** — per-backend `.bat` / `.sh` scripts.
        - **`tray/`** — Windows system-tray launcher (silent pythonw).
        - **`config/models.yaml`** — host + model registry; `roles:` section.
        - **`docs/frontier/`** — research brief + monthly runs.
        - **`.claude/commands/`** — slash commands for Claude Code.

        ### Requirements

        - The **`claude`** CLI on `PATH` (Claude Code must be installed and logged in).
        - Python deps already installed in `.venv` from `requirements.txt`.

        ### Caveats (intentional — lightweight)

        Streaming on `POST /v1/chat/completions` (OpenAI shape) **is**
        supported — including server-side `<think>` block stripping for
        reasoning models. Anthropic-shape `POST /v1/messages` streaming
        still returns a single JSON object (event translation is on the
        backlog).

        Multi-turn for Claude / Gemini is flattened into a single
        prompt. Tool use round-trips across the Anthropic ↔ OpenAI
        shapes are not implemented for the local backends. Image
        content blocks **are** supported on the `claude-*` and
        `gemini-*` paths; local `llama-server` backends are text-only
        and return 400 with a hint to retry on a subscription route.
        Documents and extended-thinking blocks are still dropped at the
        shape boundary. See **Backlog for improvement** in `README.md`.
        """
    )
