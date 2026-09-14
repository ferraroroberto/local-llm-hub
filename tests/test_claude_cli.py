"""Tests for the isolated ``claude -p`` invocation (#603).

A hub call must not inherit the operator's Claude Code setup: no settings
sources (hooks, MCP, plugins), no CLAUDE.md, no auto-memory, no skills, and
no agent tools except ``Read`` when attachments need it.
"""

from __future__ import annotations

import os
import subprocess
from io import StringIO

import pytest

from src import claude_cli as claude_cli_mod

ISOLATION = ["--strict-mcp-config", "--setting-sources", "", "--disable-slash-commands"]


def _tools_value(args: list) -> str:
    return args[args.index("--tools") + 1]


def _assert_isolated(args: list, env: dict) -> None:
    width = len(ISOLATION)
    if not any(args[i:i + width] == ISOLATION for i in range(len(args) - width + 1)):
        pytest.fail(f"isolation flags missing from argv: {args!r}")
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert "--no-session-persistence" not in args
    assert "--bare" not in args


@pytest.fixture
def captured_run(monkeypatch):
    captured: dict = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout='{"result": "ok"}', stderr="")

    monkeypatch.setattr(claude_cli_mod.subprocess, "run", fake_run)
    return captured


def test_call_claude_text_only_disables_all_tools(captured_run, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_AUTO_MEMORY", raising=False)

    envelope = claude_cli_mod.call_claude("hi", model="claude-haiku-4-5", system="terse")

    assert envelope == {"result": "ok"}
    args = captured_run["args"]
    assert args[:4] == ["claude", "-p", "--output-format", "json"]
    _assert_isolated(args, captured_run["env"])
    assert _tools_value(args) == ""
    assert args[args.index("--model") + 1] == "claude-haiku-4-5"
    assert args[args.index("--system-prompt") + 1] == "terse"
    assert "--add-dir" not in args
    assert captured_run["input"] == "hi"
    assert captured_run["creationflags"] == claude_cli_mod.NO_WINDOW
    # The child env is a copy: the hub's own environment stays untouched.
    assert "CLAUDE_CODE_DISABLE_AUTO_MEMORY" not in os.environ
    assert captured_run["env"]["PATH"] == os.environ["PATH"]


def test_call_claude_attachments_enable_only_read(captured_run, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG")

    claude_cli_mod.call_claude("what is this?", attachments=[image])

    args = captured_run["args"]
    _assert_isolated(args, captured_run["env"])
    assert _tools_value(args) == "Read"
    assert args[args.index("--add-dir") + 1] == str(tmp_path.resolve())
    assert captured_run["input"] == (
        f"Attached files:\n- {image.resolve()}\n\nwhat is this?"
    )


class _FakeProcess:
    def __init__(self) -> None:
        self.stdin = StringIO()
        self.stdout = StringIO('{"type":"result"}\n')
        self.stderr = StringIO()

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


@pytest.mark.parametrize(
    ("attachments", "tools"),
    [(False, ""), (True, "Read")],
)
def test_call_claude_stream_is_isolated(monkeypatch, tmp_path, attachments, tools):
    captured: dict = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return _FakeProcess()

    monkeypatch.setattr(claude_cli_mod.subprocess, "Popen", fake_popen)
    files = [tmp_path / "doc.pdf"] if attachments else None

    records = list(claude_cli_mod.call_claude_stream("hi", attachments=files))

    assert records == [{"type": "result"}]
    args = captured["args"]
    assert args[:6] == [
        "claude", "-p", "--output-format", "stream-json",
        "--include-partial-messages", "--verbose",
    ]
    _assert_isolated(args, captured["env"])
    assert _tools_value(args) == tools
    assert ("--add-dir" in args) is attachments
