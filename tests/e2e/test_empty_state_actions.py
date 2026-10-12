"""Empty states say what to do, and no section reads empty before it has loaded (#647, J-09).

The design review's judgment pass flags an empty list whose sentence neither
sits beside a control nor names the control that fills it, and a table that
shows only its header row. On the Code tab the breakdowns stayed header-only
until the first usage summary arrived, and "No sessions found" was asserted
before anything had been read. Since #739 By project is a card of rows and By
model and the recent sessions open as sheets from it.
"""

from __future__ import annotations

import time

PHONE = {"width": 390, "height": 844}


def test_code_cards_wait_for_the_first_summary_then_name_their_control(page, admin_url):
    held = []
    released = []

    def _hold(route):
        # Hold requests until the test has looked at the pre-load state, then
        # let everything (including the 30 s poll) straight through.
        if released:
            route.continue_()
        else:
            held.append(route)

    page.route("**/admin/api/code/usage/summary*", _hold)
    page.set_viewport_size(PHONE)
    page.goto(admin_url, wait_until="domcontentloaded")
    page.click("#tabCodeUsage")
    page.wait_for_selector("#paneCodeUsage", state="visible")

    deadline = time.monotonic() + 10
    while not held and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert held, "the Code tab never asked for its usage summary"

    # Before the summary lands: one honest "reading" line, no header-only tables,
    # no "No sessions found" claim.
    page.wait_for_selector("#cldPending", state="visible", timeout=5000)
    for card in ("#cldProjectCard", "#cldChartsCard"):
        assert page.locator(card).is_hidden(), f"{card} is showing before the first summary"

    released.append(True)
    for route in held:
        route.continue_()
    page.wait_for_selector("#cldPending", state="hidden", timeout=10000)

    # After it lands (the e2e hub has no usage history): every empty state is
    # visible where it lives (the card, or the sheet its row opens) and names
    # the control that changes it (the period switch or agent filter above) or
    # carries its own action button.
    for opener, empty in (
        (None, "#cldProjectEmpty"),
        ("#cldModelOpenBtn", "#cldModelEmpty"),
        ("#cldSessionsOpenBtn", "#cldSessionsEmpty"),
    ):
        if opener:
            page.click(opener)
        page.wait_for_selector(empty, state="visible", timeout=5000)
        text = page.locator(f"{empty} .empty-state-message").inner_text().lower()
        if opener:
            page.keyboard.press("Escape")
            page.wait_for_selector(empty, state="hidden", timeout=3000)
        has_action = page.locator(f"{empty} .empty-state-action").count() > 0
        assert has_action or "above" in text, f"{empty} names no control: {text!r}"
        assert "today" not in text, f"{empty} still says 'today' for every period: {text!r}"


def test_hub_and_otel_empty_lists_offer_a_test_request(page, admin_url):
    # The OTel pane's empty trace list was retired with it (#741); Hub
    # Activity's is the one left.
    page.goto(admin_url, wait_until="domcontentloaded")
    page.wait_for_selector("#paneHub", state="attached")
    for empty in ("#liveRequestsEmpty",):
        action = page.locator(f"{empty} .empty-state-action")
        assert action.count() == 1, f"{empty} has no action"
        assert action.get_attribute("data-open-tab") == "playground"


def test_hub_counters_and_errors_empties_name_what_to_do(page, admin_url):
    page.goto(admin_url, wait_until="domcontentloaded")
    page.wait_for_selector("#paneHub", state="attached")
    # The e2e hub has served no model traffic, so both are in their empty state.
    page.wait_for_function(
        "(document.querySelector('#countersTable tbody')?.textContent || '').includes('No requests yet')",
        timeout=10000,
    )
    counters = page.locator("#countersTable tbody").text_content() or ""
    assert "No requests yet" in counters, counters
    assert "Playground" in counters, f"counters empty row names no control: {counters!r}"
    # The errors empty state carries its own action (a card name is not a control).
    action = page.locator("#recentErrorsEmpty .empty-state-action")
    assert action.count() == 1, "recent errors empty state has no action"
    assert action.get_attribute("data-open-tab") == "playground"


def test_playground_chat_empty_card_shows_only_its_sentence(page, admin_url):
    """The Chat result card's static Reply/Usage headings are not content (#759, J-09)."""
    page.route(
        "**/admin/api/playground/send",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"text": "An invented reply.", "usage": {}}'),
    )
    page.set_viewport_size(PHONE)
    page.goto(admin_url, wait_until="load")
    page.click("#tabPlayground")
    page.wait_for_selector("#chatResult", state="visible", timeout=5000)
    page.wait_for_function(
        "document.getElementById('playgroundModel').options.length > 0", timeout=10000)

    card = page.locator("#chatResult")
    empty = card.locator("[data-empty]")
    assert empty.is_visible()
    assert "Send" in empty.inner_text(), "the sentence names the control that fills it"
    assert card.locator(".opt-group-title").count() == 2
    assert card.locator(".opt-group-title:visible").count() == 0
    assert page.locator("#playgroundReply").is_hidden()

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
