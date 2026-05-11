"""Unit tests for src.gemini_cli — subprocess fully mocked."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import gemini_cli


def _fake_proc(stdout: str = "pong", stderr: str = "", returncode: int = 0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


@pytest.fixture(autouse=True)
def _stub_which(monkeypatch):
    """Pretend `gemini` is on PATH so call_gemini reaches subprocess.

    Tests that exercise the missing-CLI branch override this with their
    own monkeypatch.
    """
    monkeypatch.setattr(gemini_cli.shutil, "which", lambda name: "/fake/gemini")


def test_call_gemini_passes_model_and_stdin(monkeypatch):
    captured = {}

    def fake_run(args, **kw):
        captured["args"] = args
        captured["input"] = kw.get("input")
        return _fake_proc(stdout="hi there")

    monkeypatch.setattr(subprocess, "run", fake_run)

    env = gemini_cli.call_gemini("ping", model="gemini-3.1-pro")
    assert env["result"] == "hi there"
    assert env["is_error"] is False
    assert env["stop_reason"] == "end_turn"

    # args[0] is the resolved binary path from shutil.which.
    assert captured["args"][0] == "/fake/gemini"
    assert "-p" in captured["args"]
    assert "-m" in captured["args"]
    assert captured["args"][captured["args"].index("-m") + 1] == "gemini-3.1-pro"
    # Prompt goes on stdin (no command-line length limit).
    assert "ping" in captured["input"]


def test_call_gemini_folds_system_into_prompt(monkeypatch):
    captured = {}

    def fake_run(args, **kw):
        captured["input"] = kw.get("input")
        return _fake_proc(stdout="ok")

    monkeypatch.setattr(subprocess, "run", fake_run)

    gemini_cli.call_gemini("the question", model="gemini-3-flash", system="Answer briefly.")
    # System prompt prepended; gemini CLI has no separate --system flag.
    assert "[System]" in captured["input"]
    assert "Answer briefly." in captured["input"]
    assert "the question" in captured["input"]


def test_call_gemini_image_refs_use_at_syntax(monkeypatch, tmp_path):
    captured = {}

    def fake_run(args, **kw):
        captured["input"] = kw.get("input")
        return _fake_proc(stdout="image described")

    monkeypatch.setattr(subprocess, "run", fake_run)

    img = tmp_path / "pic.png"
    img.write_bytes(b"fake-png-bytes")
    gemini_cli.call_gemini(
        "what is this?",
        model="gemini-3.1-pro",
        images=[img],
    )
    body = captured["input"]
    # @<absolute path> with forward slashes (POSIX-style, works on Windows).
    assert "@" in body
    assert img.resolve().as_posix() in body
    assert "what is this?" in body


def test_call_gemini_missing_cli_raises(monkeypatch):
    # Override the autouse fixture: shutil.which now reports the CLI is
    # missing, which should short-circuit before subprocess.run is called.
    monkeypatch.setattr(gemini_cli.shutil, "which", lambda name: None)

    with pytest.raises(gemini_cli.GeminiCLIError) as ei:
        gemini_cli.call_gemini("hi")
    assert "PATH" in str(ei.value)


def test_call_gemini_nonzero_exit_raises(monkeypatch):
    def fake_run(args, **kw):
        return _fake_proc(stdout="", stderr="quota exceeded", returncode=1)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(gemini_cli.GeminiCLIError) as ei:
        gemini_cli.call_gemini("hi")
    assert "quota exceeded" in str(ei.value)


def test_call_gemini_empty_stdout_raises(monkeypatch):
    def fake_run(args, **kw):
        return _fake_proc(stdout="   \n", stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(gemini_cli.GeminiCLIError) as ei:
        gemini_cli.call_gemini("hi")
    assert "empty stdout" in str(ei.value)
