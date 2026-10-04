"""Form fields draw their boundary in the control-border token (#647, COLOR-03).

The hairline ``--line`` is 1.4:1 against a card, under WCAG 1.4.11's 3:1 floor
for a component boundary; ``--control-border`` clears it. The Playground holds
one of every field kind the app styles by hand.
"""

from __future__ import annotations

import pytest

FIELDS = [
    "#playgroundModel",
    "#playgroundSystem",
    "#playgroundPrompt",
    "#playgroundMaxTokens",
    "#imageModel",
    "#imagePrompt",
    "#imageSize",
    "#ttsModel",
]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_playground_fields_use_the_control_border(page, admin_url, theme):
    page.goto(admin_url, wait_until="load")
    page.wait_for_selector("#tabPlayground", state="visible", timeout=5000)
    page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
    page.click("#tabPlayground")
    page.wait_for_selector("#panePlayground .home-head", state="visible", timeout=5000)

    # Resolve the token through a probe element so the comparison is in the
    # browser's own computed rgb() form, not a hex string.
    token = page.evaluate(
        """() => {
            const probe = document.createElement('div');
            probe.style.borderTop = '1px solid var(--control-border)';
            document.body.appendChild(probe);
            const c = getComputedStyle(probe).borderTopColor;
            probe.remove();
            return c;
        }"""
    )
    for sel in FIELDS:
        color = page.evaluate(
            "sel => getComputedStyle(document.querySelector(sel)).borderTopColor", sel
        )
        assert color == token, f"{sel}: border {color}, expected control-border {token}"
