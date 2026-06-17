"""Unit tests for the lazy whisper proxy's default-language injection (#128).

whisper-server forces each request's language to ``en`` unless the request
body carries one — its launch-level ``--language`` flag does *not* change
the per-request default. So a row that wants unbiased auto-detection
(``whisper_vanilla``: ``--language auto``) must have that value injected
into requests that omit ``language``. ``_default_language_from_args`` reads
the value off the row's args; rows without the flag (e.g.
``whisper_translate``) return ``None`` and are left untouched.
"""

from __future__ import annotations

import os

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "pc-cuda")

from src.whisper_translate_proxy import _default_language_from_args  # noqa: E402


def test_language_auto_extracted_from_args():
    args = ["--language", "auto", "--threads", "4", "--max-context", "0"]
    assert _default_language_from_args(args) == "auto"


def test_short_flag_extracted():
    assert _default_language_from_args(["-l", "es"]) == "es"


def test_none_when_flag_absent():
    # The translate row carries no --language; it must be left untouched.
    assert _default_language_from_args(["--max-context", "0", "--suppress-nst"]) is None


def test_none_for_empty_args():
    assert _default_language_from_args([]) is None


def test_trailing_flag_without_value_is_ignored():
    # A dangling --language with no following value must not IndexError.
    assert _default_language_from_args(["--suppress-nst", "--language"]) is None
