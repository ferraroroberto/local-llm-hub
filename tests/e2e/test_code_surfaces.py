"""Code text is sized and filled to the design system (#784, COMP-17).

A listing (the ``.logpane`` ``pre`` behind the Hub server log, the playground
reply and the trace detail) is 13px on the ``card`` surface with a hairline; an
inline session-id chip is 0.875em of the line around it on ``neutral-soft``.
Neither may sit on ``--card-off`` (canvas-subtle), which is true black inside a
dark card. The elements are built in place so the check needs no live data.
"""

from __future__ import annotations

import pytest

_MEASURE = """
() => {
  const root = document.documentElement;
  const probe = document.createElement('div');
  probe.innerHTML =
    '<pre class="logpane">listing</pre>' +
    '<ul class="cld-sessions"><li><div class="cld-sess-meta">' +
    '<code>0123abcd</code></div></li></ul>';
  document.body.appendChild(probe);
  const px = (el, p) => parseFloat(getComputedStyle(el)[p]);
  const rgb = (el, p) => getComputedStyle(el)[p];
  const swatch = (token) => {
    const s = document.createElement('i');
    s.style.background = `var(${token})`;
    document.body.appendChild(s);
    const v = getComputedStyle(s).backgroundColor;
    s.remove();
    return v;
  };
  const pre = probe.querySelector('.logpane');
  const meta = probe.querySelector('.cld-sess-meta');
  const code = probe.querySelector('code');
  const out = {
    preSize: px(pre, 'fontSize'),
    preBg: rgb(pre, 'backgroundColor'),
    preBorder: px(pre, 'borderTopWidth'),
    codeSize: px(code, 'fontSize'),
    metaSize: px(meta, 'fontSize'),
    codeBg: rgb(code, 'backgroundColor'),
    card: swatch('--card'),
    cardOff: swatch('--card-off'),
    neutralSoft: swatch('--neutral-soft'),
  };
  probe.remove();
  return out;
}
"""


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_listing_and_session_chip_follow_the_code_recipes(page, admin_url, theme):
    page.goto(admin_url, wait_until="load")
    page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
    m = page.evaluate(_MEASURE)

    # code-listing: 13px, on the card surface, on a hairline.
    assert m["preSize"] == 13.0
    assert m["preBg"] == m["card"]
    assert m["preBg"] != m["cardOff"]
    assert m["preBorder"] == 1.0

    # code-inline: 0.875em of the surrounding text, on neutral-soft. The meta
    # line is body-sm, so the chip never drops under 11px (TYPE-03, #810).
    assert m["metaSize"] == 14.0
    assert m["codeSize"] == pytest.approx(m["metaSize"] * 0.875, abs=0.01)
    assert m["codeBg"] == m["neutralSoft"]
    assert m["codeBg"] != m["cardOff"]
