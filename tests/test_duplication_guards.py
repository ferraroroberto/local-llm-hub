"""Guards for small behaviours that must have exactly one implementation.

The static ratchet only sees copy-paste blocks; these parallel implementations
drift silently (a missing timeout, different thresholds), so each is pinned here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_install_scripts_download_through_lib_only():
    # urlretrieve has no timeout: a stalled fetch would hang an install forever.
    offenders = [
        p.name for p in (ROOT / "scripts").glob("*.py") if "urlretrieve" in p.read_text("utf-8")
    ]
    assert offenders == []


def test_duration_formatter_defined_only_in_api_js():
    pattern = re.compile(r"function\s+fmt\w*(?:Duration|Uptime|Age)\w*\s*\(")
    offenders = [
        p.name for p in (ROOT / "app_web" / "static").glob("*.js")
        if p.name != "api.js" and pattern.search(p.read_text("utf-8"))
    ]
    assert offenders == []


def test_parse_tokens_matches_streaming_framing():
    from src.tts_engines import OrpheusEngine
    from src.tts_engines.orpheus import _SNAC_CODEBOOK

    text = "<custom_token_4>" + "".join(
        f"<custom_token_{code + 10 + (pos % 7) * _SNAC_CODEBOOK}>"
        for pos, code in enumerate([5, 6, 7, 8, 9, 10, 11, 12])
    )
    assert OrpheusEngine._parse_tokens(text) == list(OrpheusEngine._iter_token_ids([text]))
    assert OrpheusEngine._parse_tokens(text) == [5, 6, 7, 8, 9, 10, 11, 12]


class _FakeStream:
    def __init__(self, body: bytes, fail_after: int | None = None):
        self.headers = {"Content-Length": str(len(body))}
        self._body = body
        self._fail_after = fail_after

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n):
        if self._fail_after is not None and not self._body:
            raise OSError("connection reset")
        chunk, self._body = self._body[:n], self._body[n:]
        return chunk


def test_download_skips_existing_and_replaces_atomically(tmp_path, monkeypatch):
    from scripts import _lib

    dest = tmp_path / "sub" / "f.bin"
    calls: list[tuple] = []

    def _open(url, timeout):
        calls.append((url, timeout))
        return _FakeStream(b"payload")

    monkeypatch.setattr(_lib.urllib.request, "urlopen", _open)

    _lib.download("http://x/f", dest, skip_existing=True)
    assert dest.read_bytes() == b"payload"
    assert calls == [("http://x/f", 120)]
    assert not dest.with_suffix(".bin.tmp").exists()

    _lib.download("http://x/f", dest, skip_existing=True)
    assert len(calls) == 1


def test_download_failure_leaves_no_partial_file(tmp_path, monkeypatch):
    from scripts import _lib

    dest = tmp_path / "f.bin"
    monkeypatch.setattr(
        _lib.urllib.request, "urlopen",
        lambda url, timeout: _FakeStream(b"", fail_after=0),
    )

    with pytest.raises(OSError):
        _lib.download("http://x/f", dest)
    assert not dest.exists()
    assert not dest.with_suffix(".bin.tmp").exists()


class _FakeResp:
    def __init__(self, status: int, json_body=None, text: str = ""):
        self.status_code = status
        self.is_success = status < 400
        self.text = text
        self._json = json_body

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


@pytest.mark.parametrize(
    ("resp", "expected"),
    [
        (_FakeResp(500, {"error": {"message": "envelope"}, "detail": "d"}), "envelope"),
        (_FakeResp(400, {"detail": "plain detail"}), "plain detail"),
        (_FakeResp(502, {"detail": ""}, text='{"detail": ""}'), '{"detail": ""}'),
        (_FakeResp(503, text="raw text"), "raw text"),
        (_FakeResp(504), "HTTP 504"),
        (_FakeResp(400, ["not", "a", "dict"], text="list body"), "list body"),
    ],
)
def test_upstream_detail(resp, expected):
    from app_web.routers.playground import _upstream_detail

    assert _upstream_detail(resp) == expected
