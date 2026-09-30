"""Unit tests for src.gemini_cli — the Antigravity CLI (`agy`) wrapper.

The ConPTY interaction (`_switch_model`, `_print_call`) is mocked; these
tests cover the envelope shape, prompt assembly, model-switch gating,
and the pure picker/ANSI parsing helpers. No real `agy` process runs.
"""

from __future__ import annotations

import pytest

from src import gemini_cli


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Pretend `agy` is on PATH and reset the remembered model per test."""
    monkeypatch.setattr(gemini_cli.shutil, "which", lambda name: "/fake/agy")
    gemini_cli._current_model = None
    gemini_cli._catalogue = None
    yield
    gemini_cli._current_model = None
    gemini_cli._catalogue = None


def _stub_calls(monkeypatch, captured, reply="hi there"):
    """Replace the ConPTY-driven helpers with capturing fakes."""
    def fake_switch(exe, target, timeout=120.0):
        captured.setdefault("switches", []).append(target)

    def fake_print(exe, prompt, cwd, timeout, add_dirs=None):
        captured["prompt"] = prompt
        captured["cwd"] = cwd
        captured["exe"] = exe
        captured["add_dirs"] = add_dirs
        return reply

    monkeypatch.setattr(gemini_cli, "_switch_model", fake_switch)
    monkeypatch.setattr(gemini_cli, "_print_call", fake_print)


def test_call_gemini_switches_model_and_returns_envelope(monkeypatch):
    captured = {}
    _stub_calls(monkeypatch, captured)

    env = gemini_cli.call_gemini("ping", model="Gemini 3.1 Pro")

    assert env["result"] == "hi there"
    assert env["is_error"] is False
    assert env["stop_reason"] == "end_turn"
    assert env["usage"] == {"input_tokens": 0, "output_tokens": 0}
    # First call to a model triggers exactly one switch to that label.
    assert captured["switches"] == ["Gemini 3.1 Pro"]
    assert captured["exe"] == "/fake/agy"
    assert "ping" in captured["prompt"]


def test_call_gemini_skips_switch_when_model_unchanged(monkeypatch):
    captured = {}
    _stub_calls(monkeypatch, captured)

    gemini_cli.call_gemini("one", model="Gemini 3.5 Flash")
    gemini_cli.call_gemini("two", model="Gemini 3.5 Flash")

    # The model is global persisted state — switch only on a change.
    assert captured["switches"] == ["Gemini 3.5 Flash"]


def test_call_gemini_folds_system_into_prompt(monkeypatch):
    captured = {}
    _stub_calls(monkeypatch, captured)

    gemini_cli.call_gemini("the question", model="Gemini 3.5 Flash",
                           system="Answer briefly.")
    # `agy -p` has no separate system flag — system is folded in.
    assert "[System]" in captured["prompt"]
    assert "Answer briefly." in captured["prompt"]
    assert "the question" in captured["prompt"]


def test_call_gemini_image_refs_use_at_syntax(monkeypatch, tmp_path):
    captured = {}
    _stub_calls(monkeypatch, captured, reply="image described")

    img = tmp_path / "pic.png"
    img.write_bytes(b"fake-png-bytes")
    gemini_cli.call_gemini("what is this?", model="Gemini 3.1 Pro",
                           attachments=[img])

    # Attachments are referenced by basename; cwd is set to their parent dir;
    # that dir is also added to agy's workspace via --add-dir (issue #63) so
    # the reference resolves in-workspace instead of triggering a disk search.
    assert f"@{img.name}" in captured["prompt"]
    assert "what is this?" in captured["prompt"]
    assert captured["cwd"] == str(img.resolve().parent)
    assert captured["add_dirs"] == [str(img.resolve().parent)]


def test_call_gemini_missing_cli_raises(monkeypatch):
    monkeypatch.setattr(gemini_cli.shutil, "which", lambda name: None)

    with pytest.raises(gemini_cli.GeminiCLIError) as ei:
        gemini_cli.call_gemini("hi")
    assert "PATH" in str(ei.value)


def test_print_call_passes_add_dir_flags(monkeypatch):
    """_print_call adds each workspace dir as a repeated --add-dir flag (#63)."""
    seen = {}

    class _FakePty:
        def __init__(self, args, cwd=None, cols=160, rows=50):
            seen["args"] = args
            seen["cwd"] = cwd

        def wait_exit(self, timeout):
            return True

        def text(self):
            return "rendered reply"

        def kill(self):
            pass

    monkeypatch.setattr(gemini_cli, "_Pty", _FakePty)
    reply = gemini_cli._print_call(
        "/fake/agy", "prompt @doc_0.pdf", "/work", 600.0,
        add_dirs=["/work", "/other"],
    )
    assert reply == "rendered reply"
    args = seen["args"]
    # Every add-dir is a separate --add-dir <value> pair, after the prompt.
    assert args.count("--add-dir") == 2
    for d in ("/work", "/other"):
        i = args.index(d)
        assert args[i - 1] == "--add-dir"
    assert seen["cwd"] == "/work"


def test_print_call_no_add_dir_when_none(monkeypatch):
    """No --add-dir flag is emitted for attachment-free calls."""
    seen = {}

    class _FakePty:
        def __init__(self, args, cwd=None, cols=160, rows=50):
            seen["args"] = args

        def wait_exit(self, timeout):
            return True

        def text(self):
            return "ok"

        def kill(self):
            pass

    monkeypatch.setattr(gemini_cli, "_Pty", _FakePty)
    gemini_cli._print_call("/fake/agy", "hello", None, 600.0)
    assert "--add-dir" not in seen["args"]


def test_parse_picker_reads_labels_and_current():
    rendered = (
        "Switch Model\n"
        "  Gemini 3.5 Flash (High)\n"
        "  Gemini 3.5 Flash (Medium)\n"
        "> Gemini 3.1 Pro      (current)\n"
        "  Claude Opus 4.6 (Thinking)\n"
        "\n"
        "Keyboard: arrows Navigate  enter Select\n"
    )
    labels, current = gemini_cli._parse_picker(rendered)
    assert labels == [
        "Gemini 3.5 Flash (High)",
        "Gemini 3.5 Flash (Medium)",
        "Gemini 3.1 Pro",
        "Claude Opus 4.6 (Thinking)",
    ]
    assert current == 2


def test_parse_picker_reads_unparenthesized_rows_with_effort_slider():
    """Live `agy` (>=1.1.8) decouples effort into a slider below the list —
    rows are bare model names with no parenthesised suffix at all. Regression
    for issue #440: the old "must contain a paren" heuristic silently dropped
    every Gemini row, so the hub reported them as "not offered" even though
    they were selectable. Fixture is a trimmed capture of the real picker
    screen.
    """
    rendered = (
        "Switch Model\n"
        "\n"
        "> Gemini 3.6 Flash            \n"
        "  Gemini 3.5 Flash\n"
        "  Gemini 3.1 Pro (current)\n"
        "  Claude Sonnet 4.6 (Thinking)\n"
        "  Claude Opus 4.6 (Thinking)\n"
        "  GPT-OSS 120B (Medium)\n"
        "\n"
        "  Effort  <>--o------o------o--<>\n"
        "       low          medium          high      \n"
        " Faster responses, lighter reasoning\n"
        "\n"
        "Keyboard: up/down Navigate  left/right Effort  enter Select  esc Go Back\n"
    )
    labels, current = gemini_cli._parse_picker(rendered)
    assert labels == [
        "Gemini 3.6 Flash",
        "Gemini 3.5 Flash",
        "Gemini 3.1 Pro",
        "Claude Sonnet 4.6 (Thinking)",
        "Claude Opus 4.6 (Thinking)",
        "GPT-OSS 120B (Medium)",
    ]
    assert current == 2


def test_parse_picker_empty_when_no_block():
    assert gemini_cli._parse_picker("no picker here") == ([], 0)


def test_strip_ansi_keeps_only_text():
    raw = "\x1b[1t\x1b[c\x1b[?9001hPONG\r\n"
    assert gemini_cli._strip_ansi(raw).strip() == "PONG"


# Trimmed capture of the real `/model` picker from agy 1.2.13 (#629): the
# header is followed by a `Search:` input and a box-drawing rule *before* the
# model rows. The old parser read those two lines as the only "models".
_PICKER_SCREEN_AGY_1_2 = (
    "Switch Model\n"
    "\n"
    "  Search:  \n"
    " ────────────────────\n"
    "\n"
    "  Gemini 3.8 Flash\n"
    "  Gemini 3.7 Flash\n"
    "  Gemini 3.6 Flash\n"
    "> Gemini 3.1 Pro (current)\n"
    "  Claude Sonnet 4.6 (Thinking)\n"
    "  Claude Opus 4.6 (Thinking)\n"
    "  GPT-OSS 120B (Medium)\n"
    "\n"
    "  Effort  ◂            ◉──────────────────────○            ▸\n"
    "           low                   high          \n"
    " Faster responses, lighter reasoning — great for simpler tasks\n"
    "\n"
    "Keyboard: ↑/↓ Navigate  ←/→ Effort  enter Select  esc Go Back\n"
)


def test_parse_picker_skips_search_box_and_rule():
    labels, current = gemini_cli._parse_picker(_PICKER_SCREEN_AGY_1_2)
    assert labels == [
        "Gemini 3.8 Flash",
        "Gemini 3.7 Flash",
        "Gemini 3.6 Flash",
        "Gemini 3.1 Pro",
        "Claude Sonnet 4.6 (Thinking)",
        "Claude Opus 4.6 (Thinking)",
        "GPT-OSS 120B (Medium)",
    ]
    assert current == 3


def test_parse_picker_search_box_with_query_text():
    rendered = (
        "Switch Model\n\n  Search: pro\n ──────\n\n"
        "> Gemini 3.1 Pro (current)\n\nKeyboard: x\n")
    assert gemini_cli._parse_picker(rendered) == (["Gemini 3.1 Pro"], 0)


class _PickerPty:
    """Fake ConPTY that 'renders' a fixed picker screen."""

    def __init__(self, screen: str):
        self._screen = screen
        self.writes: list = []

    def wait_for(self, markers, timeout):
        return markers[0]

    def text(self):
        return self._screen

    def write(self, keys):
        self.writes.append(keys)

    def kill(self):
        pass


def _fake_picker(monkeypatch, screen):
    pty = _PickerPty(screen)
    monkeypatch.setattr(gemini_cli, "_Pty", lambda *a, **k: pty)
    monkeypatch.setattr(gemini_cli.time, "sleep", lambda s: None)
    return pty


def test_switch_model_records_catalogue_and_logs(monkeypatch, caplog):
    _fake_picker(monkeypatch, _PICKER_SCREEN_AGY_1_2)
    with caplog.at_level("INFO", logger=gemini_cli.logger.name):
        gemini_cli._switch_model("/fake/agy", "Gemini 3.6 Flash")
    assert gemini_cli.is_model_offered("Gemini 3.6 Flash") is True
    assert gemini_cli.is_model_offered("Gemini 3.5 Flash") is False
    assert "Gemini 3.8 Flash" in caplog.text


def test_switch_model_not_offered_lists_real_catalogue(monkeypatch):
    _fake_picker(monkeypatch, _PICKER_SCREEN_AGY_1_2)
    with pytest.raises(gemini_cli.GeminiCLIError) as exc:
        gemini_cli._switch_model("/fake/agy", "Gemini 3.5 Flash")
    msg = str(exc.value)
    assert "not offered by agy" in msg
    assert "Gemini 3.1 Pro" in msg and "Search:" not in msg


def test_switch_model_unreadable_picker_reports_catalogue_unavailable(monkeypatch):
    gemini_cli._catalogue = ["Gemini 3.1 Pro"]
    _fake_picker(monkeypatch, "Switch Model\n\n  Search:  \n ─────\n\nKeyboard: x\n")
    with pytest.raises(gemini_cli.GeminiCLIError, match="catalogue unavailable"):
        gemini_cli._switch_model("/fake/agy", "Gemini 3.1 Pro")
    # Unknown, not "not offered": nothing gets hidden on an unreadable screen.
    assert gemini_cli.is_model_offered("Gemini 3.1 Pro") is None


def test_is_model_offered_unknown_before_any_read():
    assert gemini_cli.is_model_offered("Gemini 3.1 Pro") is None
