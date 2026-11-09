"""The Playground tab: one composer, its modes, its empty states and its geometry.

Every request is stubbed or held by the test, so no model backend is reached.
Each phase runs in its own browser context.
"""

from __future__ import annotations

from tests.e2e._flow import DESKTOP, PHONE, fresh_page, snapshot
from tests.e2e._surface import surface

_BLOCK_MODE_STORAGE = """
(() => {
  const get = Storage.prototype.getItem, set = Storage.prototype.setItem;
  Storage.prototype.getItem = function (k) {
    if (k === 'llmhub.playground.mode') throw new Error('blocked');
    return get.call(this, k);
  };
  Storage.prototype.setItem = function (k, v) {
    if (k === 'llmhub.playground.mode') throw new Error('blocked');
    return set.call(this, k, v);
  };
})();
"""

_TTS_MODELS = {
    "models": [
        {
            "id": "piper", "display_name": "piper-tts", "engine": "piper", "reachable": True,
            "capabilities": {
                "languages": [{"id": "en-US", "label": "English (US)"}],
                "voices": [{"id": "amy", "label": "Amy", "language": "en-US", "gender": "female"}],
                "default_language": "en-US",
                "default_voice": "amy",
                "sample_text": {"en-US": "Hello sample."},
                "controls": {"speed": True, "stream": False, "exaggeration": False, "cfg_weight": False},
            },
        },
        {
            "id": "kokoro", "display_name": "kokoro-tts", "engine": "kokoro", "reachable": True,
            "capabilities": {
                "languages": [
                    {"id": "en-US", "label": "English (US)"},
                    {"id": "es", "label": "Spanish"},
                ],
                "voices": [
                    {"id": "am_michael", "label": "Michael", "language": "en-US", "gender": "male"},
                    {"id": "ef_dora", "label": "Dora", "language": "es", "gender": "female"},
                    {"id": "em_alex", "label": "Alex", "language": "es", "gender": "male"},
                ],
                "default_language": "en-US",
                "default_voice": "am_michael",
                "sample_text": {"en-US": "Hello sample.", "es": "Hola de muestra."},
                "controls": {"speed": True, "stream": False, "exaggeration": False, "cfg_weight": False},
            },
        },
        {"id": "orpheus", "display_name": "orpheus-tts", "engine": "orpheus",
         "reachable": False, "capabilities": {}},
    ]
}

_FIELDS = [
    "#playgroundModel", "#playgroundSystem", "#playgroundPrompt", "#playgroundMaxTokens",
    "#imageModel", "#imagePrompt", "#imageSize", "#ttsModel",
]

_REPLY = '{"text": "An invented reply.", "usage": {}}'


def _shown_mode(page):
    return page.evaluate(
        "() => [...document.querySelectorAll('#panePlayground .play-composer')]"
        ".filter(c => !c.hidden).map(c => c.dataset.mode)"
    )


def _wait_for_models(page):
    page.wait_for_function(
        "document.getElementById('playgroundModel').options.length > 0", timeout=10000)


def _one_composer_remembers_its_mode(page, admin_url):
    """Chat on a first visit, the last mode remembered across a reload, and a
    request in flight survives a mode switch, its result shown only in its own
    mode."""
    held = []
    page.route("**/admin/api/playground/send", lambda route: held.append(route))
    page.goto(admin_url, wait_until="load")
    page.click("#tabPlayground")
    page.wait_for_selector("#panePlayground", state="visible", timeout=3000)
    # Boot fans out several fetches at once, including install_status, the slow
    # one: the window covers the cold-cache case.
    _wait_for_models(page)
    assert _shown_mode(page) == ["chat"]
    assert page.locator("#panePlayground details.card--collapsible").count() == 0
    assert page.locator("#playgroundPrompt").is_visible()

    page.fill("#playgroundPrompt", "An invented test prompt.")
    page.click("#playgroundSendBtn")
    page.wait_for_function("() => true")
    for _ in range(60):
        if held:
            break
        page.wait_for_timeout(50)
    assert held, "the chat request never left"
    page.click('#playMode [data-mode="image"]')
    assert _shown_mode(page) == ["image"]
    held[0].fulfill(status=200, content_type="application/json", body=_REPLY)
    page.wait_for_function(
        "document.getElementById('playgroundReply').textContent === 'An invented reply.'", timeout=5000)
    assert "An invented reply." not in page.locator("#imageResult").inner_text()
    page.click('#playMode [data-mode="chat"]')
    assert page.locator("#playgroundReply").is_visible()

    # The last mode survives a reload; a blocked storage falls back to Chat.
    page.click('#playMode [data-mode="speech"]')
    page.reload(wait_until="load")
    page.click("#tabPlayground")
    assert _shown_mode(page) == ["speech"]
    assert page.locator('#playMode [data-mode="speech"]').get_attribute("aria-pressed") == "true"
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(_BLOCK_MODE_STORAGE)
    page.reload(wait_until="load")
    page.click("#tabPlayground")
    assert _shown_mode(page) == ["chat"]
    page.click('#playMode [data-mode="image"]')
    assert _shown_mode(page) == ["image"]
    assert errors == [], errors


def _speech_selectors_follow_the_model_capabilities(page, admin_url):
    page.route("**/admin/api/playground/tts_models", lambda route: route.fulfill(
        status=200, content_type="application/json", json=_TTS_MODELS))
    page.goto(admin_url, wait_until="load")
    page.click("#tabPlayground")
    page.click('#playMode [data-mode="speech"]')
    page.locator("#ttsMore").evaluate("element => { element.open = true; }")
    page.wait_for_function("document.getElementById('ttsModel').options.length === 3")

    assert page.locator("#ttsVoice").input_value() == "amy"
    assert page.locator("#ttsSpeedGroup").is_visible()
    assert page.locator("#ttsStreamGroup").is_hidden()
    assert page.locator("#ttsExaggerationGroup").is_hidden()
    assert page.locator("#ttsCfgWeightGroup").is_hidden()
    assert page.locator("#ttsFormat").is_visible()
    assert "stopped" in page.locator("#ttsModel option[value='orpheus']").text_content()
    assert page.locator("#ttsModel option[value='orpheus']").is_disabled()
    assert page.locator("#ttsCompareBtn").count() == 0

    page.locator("#ttsInput").fill("Keep my edited text.")
    page.locator("#ttsModel").select_option("kokoro")
    page.locator("#ttsLanguage").select_option("es")
    assert page.locator("#ttsInput").input_value() == "Keep my edited text."
    assert page.locator("#ttsVoice option").all_text_contents() == ["Dora · female", "Alex · male"]


def _phone_tab_shows_its_empty_card_and_privacy_line(page, admin_url, browser_name, mark):
    """The Chat result card's static Reply/Usage headings are not content; Decide
    shows its third-party privacy line without a tap, at phone width and on a
    wide window, in both themes."""
    page.route("**/admin/api/playground/send", lambda route: route.fulfill(
        status=200, content_type="application/json", body=_REPLY))
    page.goto(admin_url, wait_until="load")
    page.click("#tabPlayground")
    page.wait_for_selector("#panePlayground", state="visible", timeout=3000)
    page.wait_for_selector("#chatResult", state="visible", timeout=5000)
    _wait_for_models(page)
    snapshot(page, "playground-390x844", browser_name)
    mark("playground.tab")

    card = page.locator("#chatResult")
    empty = card.locator("[data-empty]")
    assert empty.is_visible()
    assert "Send" in empty.inner_text(), "the sentence names the control that fills it"
    assert card.locator(".opt-group-title").count() == 2
    assert card.locator(".opt-group-title:visible").count() == 0
    assert page.locator("#playgroundReply").is_hidden()
    mark("playground.chat-empty")

    page.fill("#playgroundPrompt", "An invented test prompt.")
    page.click("#playgroundSendBtn")
    page.wait_for_function(
        "document.getElementById('playgroundReply').textContent === 'An invented reply.'", timeout=5000)
    assert empty.is_hidden()
    assert card.locator(".opt-group-title:visible").count() == 2
    assert page.locator("#playgroundReply").is_visible()

    page.click("#playgroundClearBtn")
    assert empty.is_visible()
    assert card.locator(".opt-group-title:visible").count() == 0

    page.click('#playMode [data-mode="decide"]')
    for viewport in (PHONE, DESKTOP):
        page.set_viewport_size(viewport)
        for theme in ("light", "dark"):
            page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
            privacy = page.locator("#jevCard .jev-privacy")
            assert privacy.is_visible(), (viewport, theme)
            assert "third-party" in privacy.inner_text()


def _fields_and_presets_keep_the_design_geometry(page, admin_url):
    """Form fields draw their boundary in the control-border token (the hairline
    ``--line`` is 1.4:1 against a card, under the 3:1 floor for a component
    boundary), and the Max tokens track is the item plus the 3px inset on both
    sides, with the number input sized to the track and never the reverse."""
    page.goto(admin_url, wait_until="load")
    page.click("#tabPlayground")
    page.wait_for_selector("#panePlayground .home-head", state="visible", timeout=5000)
    for theme in ("light", "dark"):
        page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
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
        for sel in _FIELDS:
            color = page.evaluate(
                "sel => getComputedStyle(document.querySelector(sel)).borderTopColor", sel)
            assert color == token, f"{sel}: border {color}, expected control-border {token} ({theme})"

    page.evaluate("document.getElementById('playgroundMore').open = true")
    page.wait_for_selector("#playgroundMaxTokensSeg", state="visible", timeout=3000)
    for viewport in (PHONE, {"width": 1280, "height": 900}):
        page.set_viewport_size(viewport)
        track = page.locator("#playgroundMaxTokensSeg").bounding_box()
        item = page.locator(
            '#playgroundMaxTokensSeg .segmented-item[aria-pressed="true"]').bounding_box()
        field = page.locator("#playgroundMaxTokens").bounding_box()
        assert abs(track["height"] - (item["height"] + 6)) <= 1, (viewport, track, item)
        assert abs(field["height"] - track["height"]) <= 1, (viewport, field, track)


def test_playground(browser, admin_url, browser_name):
    with fresh_page(browser, console_errors=True) as page:
        _one_composer_remembers_its_mode(page, admin_url)
    with fresh_page(browser, console_errors=True) as page:
        _speech_selectors_follow_the_model_capabilities(page, admin_url)
    with fresh_page(browser, console_errors=True, viewport=PHONE) as page:
        _phone_tab_shows_its_empty_card_and_privacy_line(
            page, admin_url, browser_name, lambda name: surface(page, name))
    with fresh_page(browser) as page:
        _fields_and_presets_keep_the_design_geometry(page, admin_url)
