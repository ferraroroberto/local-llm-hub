"""Labels and column headers are plain words, not codenames or shorthand (#647, J-07).

The design review's judgment pass flags any visible label, heading or column
header that carries a code term (JSON, a type keyword), an internal codename
(Jev) or a clipped abbreviation (Req, tok, rd). The words below are the ones
that were found; the page is read straight from the DOM so hidden tabs count.
"""

from __future__ import annotations

import re

# Whole-word, case-sensitive: "Requests" is fine, "Req" is not.
_JARGON = re.compile(r"\b(JSON|noul|Jev|Req|Err|tok|rd|cr|p50|p95|PCM)\b")

_LABEL_SELECTORS = "th, label, .collapse-title, .card-header h2, .card-header h3"


def test_labels_and_column_headers_use_plain_words(page, admin_url):
    page.goto(admin_url, wait_until="load")
    page.wait_for_selector("#paneHub", state="attached", timeout=5000)

    texts = page.evaluate(
        r"""sel => [...document.querySelectorAll(sel)]
            .map(el => (el.textContent || '').replace(/\s+/g, ' ').trim())
            .filter(Boolean)""",
        _LABEL_SELECTORS,
    )
    assert texts, "no labels found: selector drifted"
    offenders = sorted({t for t in texts if _JARGON.search(t)})
    assert not offenders, f"labels with jargon or abbreviations: {offenders}"
