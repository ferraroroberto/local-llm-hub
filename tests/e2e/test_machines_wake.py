"""The Machines tab's rows and the machine sheet behind them.

The SPA contract is pinned with route interception on
``GET /admin/api/machines/status`` and the wake / sync endpoints, so no real
fleet is ever probed. Phases run at a touch-enabled phone viewport (the same
coarse-pointer branch the rest of the Machines tab is validated against), each in
its own browser context.

* Wake is the row's one visible verb on a **down** (or dormant) machine whose
  row advertises ``actions.wake`` (the MAC gate); an **up** or **self** machine
  never shows it even when the flag is true. Wake fires the POST with no confirm
  dialog, is not a danger button and toasts the outcome.
* An up machine's row says so with the avatar badge and no success chip; tapping
  it opens the machine sheet: the peer hub's out-of-sync state with both builds,
  Sync, the health and resource sections, and Reboot / Shut down last behind the
  confirm dialog; a down hub peer offers Start hub instead; addresses render as
  code; on a wide window the same body fills the detail pane.
* Sync is offered only when it can help and reports the outcome; a long reason
  wraps inside the phone sheet.
* A dormant machine is a neutral row sorted after the live ones, raises no alert
  and is not counted in "N offline".
"""

from __future__ import annotations

from tests.e2e._flow import PHONE, fresh_page, fulfil_json
from tests.e2e._surface import surface

WIDE_VIEWPORT = {"width": 1440, "height": 900}

# A fixed fleet spanning every branch of the Wake verb's show/hide rule and the
# peer-hub states the sheet carries (invented names and builds).
FAKE_STATUS = {
    "machines": [
        {
            # This host: never wakeable.
            "id": "tower", "display_name": "Tower", "icon": "monitor",
            "state": "self", "is_host": True, "runs_hub": True,
            "role": "hub", "actions": {"wake": False}, "hub": None,
            # The health watch reports a leaking process (invented name).
            "health": {"enabled": True, "verdict": "act", "sampled_at": 0,
                       "reason": "leaky.exe at 1.2 M handles, +90 k/h (+1 more)",
                       "fix": "Restart leaky.exe",
                       "findings": [
                           {"level": "act", "kind": "process", "reason": "leaky.exe at 1.2 M handles, +90 k/h",
                            "fix": "Restart leaky.exe"},
                           {"level": "watch", "kind": "system", "reason": "System handles 2.4 M, mostly leaky.exe",
                            "fix": "Restart leaky.exe"},
                       ]},
            # The resource watch is fine; the weekly stall summary carries one
            # drive with stalls and one it could not confirm (invented numbers).
            "resources": {"enabled": True, "state": "ok", "sampled_at": 0, "reason": "all clear",
                          "stalls": {"state": "unknown", "line": "", "drives": [
                              {"drive": "E:", "state": "ok",
                               "line": "E: 3 stalls, 41 min total, worst 1720 ms, the last 7 days (96% measured)"},
                              {"drive": "C:", "state": "unknown",
                               "line": "C: not confirmed, latency measured for only 12% of the last 7 days"},
                          ]}},
        },
        {
            # A reachable peer that advertises wake capability but is UP: the
            # state gate must suppress the verb regardless of the flag. It runs
            # its own hub, on a different build.
            "id": "desktop", "display_name": "Desktop", "icon": "monitor",
            "state": "up", "is_host": False, "runs_hub": True,
            "role": "peer", "uptime_seconds": 7200,
            # Documentation-range address and a made-up MAC.
            "ip": "192.0.2.10", "mac": "02:00:00:00:00:01",
            "actions": {"wake": True, "reboot": True, "shutdown": True, "ssh_terminal": True, "rdp": True},
            "hub": {"reachable": True, "git_sha_match": False,
                    "local_git_sha": "aaa1111", "remote_git_sha": "bbb2222",
                    "sync": {"kind": "peer_behind", "sync_helps": True,
                             "reason": "Desktop is 2 commits behind main. Sync updates it."}},
        },
        {
            # A powered-off, MAC-equipped peer whose hub is down too: the row
            # shows Wake, the sheet offers Start hub.
            "id": "mini", "display_name": "Mac Mini", "icon": "server",
            "state": "down", "is_host": False, "runs_hub": True,
            "role": "peer", "actions": {"wake": True},
            "hub": {"reachable": False, "git_sha_match": None,
                    "local_git_sha": None, "remote_git_sha": None},
        },
        {
            # Powered off but with no MAC on file: wake unavailable, so no verb.
            "id": "laptop", "display_name": "Laptop", "icon": "laptop",
            "state": "down", "is_host": False, "runs_hub": False,
            "role": "peer", "actions": {"wake": False}, "hub": None,
        },
        {
            # Dormant + wakeable: the other state that shows the verb.
            "id": "sat", "display_name": "Satellite", "icon": "server",
            "state": "dormant", "is_host": False, "runs_hub": False,
            "role": "peer", "actions": {"wake": True}, "hub": None,
        },
    ]
}

# Intentionally off, listed ahead of a live machine to prove the sort.
DORMANT_STATUS = {"machines": [
    {"id": "tower", "display_name": "Tower", "icon": "monitor", "state": "self",
     "is_host": True, "role": "hub", "actions": {"wake": False}, "hub": None},
    {"id": "attic", "display_name": "Attic", "icon": "server", "state": "dormant",
     "answering": False, "is_host": False, "role": "peer", "actions": {"wake": True}, "hub": None},
    {"id": "den", "display_name": "Den", "icon": "monitor", "state": "up",
     "is_host": False, "role": "peer", "uptime_seconds": 600,
     "actions": {"wake": False}, "hub": None},
    # Dormant but answering again (after a Wake).
    {"id": "loft", "display_name": "Loft", "icon": "server", "state": "dormant",
     "answering": True, "is_host": False, "role": "peer", "actions": {"wake": True}, "hub": None},
]}


def _open_machines(page, admin_url, body):
    page.route("**/admin/api/machines/status", lambda route: fulfil_json(route, body))
    page.goto(admin_url, wait_until="load")
    page.click("#tabMachines")
    page.wait_for_selector("#paneMachines", state="visible", timeout=5000)
    page.wait_for_selector(".machine-row", state="visible", timeout=15000)


def _row(page, machine_id):
    return page.locator(f'.machine-row[data-machine-id="{machine_id}"]')


def _wake_btn(page, machine_id):
    return _row(page, machine_id).locator('[data-machine-action="wake"]')


def _open_sheet(page, machine_id):
    _row(page, machine_id).locator(".action-row-main").click()
    page.wait_for_selector("#machineSheet[open]", timeout=5000)
    return page.locator("#machineSheetBody")


def _wake_is_offered_only_to_a_down_machine_with_a_mac(page, mark):
    btn = _wake_btn(page, "mini")
    assert btn.count() == 1, "the down, wakeable machine should show a Wake verb"
    assert btn.is_visible()
    # A glyph-only icon-button: the name and tooltip carry the verb and the row.
    assert (btn.inner_text() or "").strip() == ""
    assert btn.get_attribute("aria-label") == "Wake Mac Mini"
    assert btn.get_attribute("title") == "Wake Mac Mini"
    assert btn.get_attribute("data-track") == "machine-wake"
    assert btn.locator('use[href="#i-power"]').count() == 1  # the vendored sprite's power glyph
    assert _wake_btn(page, "sat").count() == 1  # a dormant, wakeable machine shows it too
    # Down is readable from the row face: the avatar's down badge and the word.
    assert _row(page, "mini").locator('.action-row-avatar[data-badge="down"]').count() == 1
    assert "Offline" in _row(page, "mini").inner_text()
    assert page.locator("#machinesHeadLine").inner_text().startswith("2 offline")
    mark("machines.down-wakeable")

    assert _wake_btn(page, "laptop").count() == 0  # the MAC gate
    # The state gate is independent of the capability flag.
    assert _wake_btn(page, "desktop").count() == 0, "an up machine must never show Wake, even when actions.wake is true"
    assert _wake_btn(page, "tower").count() == 0, "the self/host row never shows Wake"


def _an_up_machine_opens_its_sheet(page):
    up = _row(page, "desktop")
    assert up.locator('.action-row-avatar[data-badge="up"]').count() == 1
    assert "Online" not in page.locator("#paneMachines").inner_text(), "no green Online chip"
    assert up.locator(".chip").inner_text() == "out of sync"

    sheet = _open_sheet(page, "desktop")
    assert "Out of sync" in sheet.inner_text()
    assert "aaa1111" in sheet.inner_text() and "bbb2222" in sheet.inner_text()
    assert "Desktop is 2 commits behind main" in sheet.locator("[data-machine-hub-reason]").inner_text()
    assert sheet.locator('[data-machine-action="sync"]').count() == 1

    # An address and a MAC are identifiers: the Connection row and its pairs show
    # them inside <code>, never as prose.
    sheet.locator(".sheet-disclosure > summary").first.click()
    codes = sheet.locator(".sheet-disclosure code").all_inner_texts()
    assert codes.count("192.0.2.10") == 2, codes  # the row value and the Address pair
    assert "02:00:00:00:00:01" in codes, codes

    # Reboot and Shut down are the last rows, and Reboot opens the confirm.
    actions = sheet.locator("[data-machine-action]").evaluate_all("els => els.map(e => e.dataset.machineAction)")
    assert actions[-2:] == ["reboot", "shutdown"], actions
    sheet.locator('[data-machine-action="reboot"]').click()
    page.wait_for_selector("#confirmDialog[open]", timeout=5000)
    page.click("#confirmDialogCloseBtn")
    page.click("#machineSheetCloseBtn")


def _this_machines_sheet_lists_its_health_and_resources(page):
    host = _row(page, "tower")
    assert host.locator(".chip").inner_text() == "needs action"  # a danger chip on the row
    sheet = _open_sheet(page, "tower")
    assert sheet.locator("[data-machine-health]").inner_text() == "Act"
    assert "System handles 2.4 M" in sheet.locator(".machine-health").inner_text()
    assert sheet.locator("[data-machine-health-fix]").inner_text() == "Suggested: Restart leaky.exe"
    # The Resources section: a fine state (no extra chip) and one stall line per drive.
    assert sheet.locator("[data-machine-resources]").inner_text() == "Fine"
    stalls = sheet.locator("[data-machine-stall]")
    assert stalls.count() == 2
    assert "3 stalls" in stalls.nth(0).inner_text() and "worst 1720 ms" in stalls.nth(0).inner_text()
    assert stalls.nth(1).get_attribute("data-tone") == "attention"
    page.click("#machineSheetCloseBtn")

    # The down hub peer's sheet offers Start hub (bootstrap) instead of Sync.
    sheet = _open_sheet(page, "mini")
    assert sheet.locator('[data-machine-action="bootstrap"]').count() == 1
    assert sheet.locator('[data-machine-action="sync"]').count() == 0
    page.click("#machineSheetCloseBtn")


def _wake_posts_without_a_confirm_and_toasts(page):
    wake_calls = []
    page.route("**/admin/api/machines/*/wake", lambda route: (
        wake_calls.append(route.request.url), fulfil_json(route, {"ok": True, "sent": True}))[-1])

    btn = _wake_btn(page, "mini")
    assert "danger" not in (btn.get_attribute("class") or "")  # non-destructive: not a danger action

    with page.expect_request(lambda req: req.url.endswith("/admin/api/machines/mini/wake") and req.method == "POST"):
        btn.click()

    assert wake_calls, "clicking Wake did not POST to the wake endpoint"
    assert page.locator("#confirmDialog[open]").count() == 0, (
        "Wake must not open the destructive-action confirm dialog")
    toast = page.locator("#toast")
    toast.wait_for(state="visible", timeout=5000)
    assert "Wake packet sent to Mac Mini" in toast.inner_text()


def _a_wide_window_fills_the_detail_pane(page, admin_url):
    _open_machines(page, admin_url, FAKE_STATUS)
    _row(page, "desktop").locator(".action-row-main").click()
    detail = page.locator("#machinesDetail")
    detail.locator('[data-machine-id="desktop"]').first.wait_for(timeout=5000)
    assert detail.is_visible()
    assert page.locator("#machineSheet[open]").count() == 0
    assert detail.locator('[data-machine-action="terminal"]').count() == 1
    assert detail.locator('[data-machine-action="shutdown"]').count() == 1
    assert "is-selected" in _row(page, "desktop").get_attribute("class")


def _hub_status(sync, match=False):
    """A one-peer status whose hub block carries ``sync``."""
    return {"machines": [{
        "id": "desktop", "display_name": "Desktop", "icon": "monitor",
        "state": "up", "is_host": False, "runs_hub": True, "role": "peer",
        "uptime_seconds": 60, "ip": "192.0.2.10", "actions": {},
        "hub": {"reachable": True, "git_sha_match": match,
                "local_git_sha": "aaa1111", "remote_git_sha": "bbb2222", "sync": sync},
    }]}


def _sync_is_offered_only_when_it_can_help(page, admin_url, mark):
    """A stale local hub gets its reason and no Sync button; a peer behind main
    gets Sync, and after it runs the status is re-read and the toast says whether
    the hubs now match; a sentence-length reason wraps within the phone sheet."""
    current = {"body": _hub_status({
        "kind": "restart_local", "sync_helps": False,
        "reason": "This hub is running an older build than its own checkout. Restart this hub."})}
    page.route("**/admin/api/machines/status", lambda route: fulfil_json(route, current["body"]))
    page.route("**/admin/api/hosts/desktop/sync", lambda route: fulfil_json(route, {"ok": True}))

    def reopen():
        page.reload(wait_until="load")
        page.click("#tabMachines")
        page.wait_for_selector(".machine-row", state="visible", timeout=15000)
        return _open_sheet(page, "desktop")

    page.goto(admin_url, wait_until="load")
    page.click("#tabMachines")
    page.wait_for_selector(".machine-row", state="visible", timeout=15000)
    sheet = _open_sheet(page, "desktop")
    assert "Restart this hub" in sheet.locator("[data-machine-hub-reason]").inner_text()
    assert sheet.locator('[data-machine-action="sync"]').count() == 0

    # The next poll finds the peer behind main: Sync appears, and running it lands
    # on an in-sync re-read.
    current["body"] = _hub_status({
        "kind": "peer_behind", "sync_helps": True,
        "reason": "Desktop is 1 commit behind main. Sync updates it."})
    sheet = reopen()
    sync_btn = sheet.locator('[data-machine-action="sync"]')
    sync_btn.wait_for(timeout=5000)
    current["body"] = _hub_status(
        {"kind": "in_sync", "sync_helps": False, "reason": "Both hubs run the same build."}, match=True)
    sync_btn.click()
    toast = page.locator("#toast")
    toast.wait_for(state="visible", timeout=5000)
    assert "Desktop hub is in sync" in toast.inner_text()

    long_reason = _hub_status({
        "kind": "peer_behind", "sync_helps": True,
        "reason": "Mac Mini M4 is 1 commit behind main. Sync updates it, then restarts "
                  "its hub so the new build is the one answering requests."})
    long_reason["machines"][0]["health"] = FAKE_STATUS["machines"][0]["health"]
    current["body"] = long_reason
    sheet = reopen()
    reason = sheet.locator("[data-machine-hub-reason]")
    assert "restarts its hub" in reason.inner_text()
    assert reason.evaluate("el => el.scrollWidth <= el.clientWidth"), "the reason overflows its own box"
    assert sheet.evaluate("el => el.scrollWidth <= el.clientWidth"), "the sheet scrolls sideways"
    box = reason.bounding_box()
    sheet_box = sheet.bounding_box()
    assert box["x"] + box["width"] <= sheet_box["x"] + sheet_box["width"] + 0.5
    assert box["height"] > 30, "a long reason should wrap onto several lines"
    mark("machines.peer-out-of-sync-phone")


def _dormant_machines_raise_no_alert_and_sort_last(page, admin_url, mark):
    _open_machines(page, admin_url, DORMANT_STATUS)

    ids = page.locator(".machine-row").evaluate_all("els => els.map(e => e.dataset.machineId)")
    assert ids == ["tower", "den", "attic", "loft"], ids  # live first, dormant after

    head = page.locator("#machinesHeadLine")
    assert head.inner_text() == "4 machines · 2 dormant"
    assert head.locator('[data-tone="danger"]').count() == 0, "no red alert"

    attic = _row(page, "attic")
    assert "Dormant" in attic.inner_text()
    assert attic.locator(".action-row-avatar[data-badge]").count() == 0, "no alive/down badge"
    assert attic.locator('[data-machine-action="wake"]').count() == 1

    loft = _row(page, "loft")
    assert "Reachable" in loft.inner_text()
    assert loft.locator('.action-row-avatar[data-badge="up"]').count() == 1
    assert loft.locator('[data-machine-action="wake"]').count() == 0, "it is up: no Wake"
    mark("machines.dormant")
    sheet = _open_sheet(page, "loft")
    assert "Switch Dormant off in Settings" in sheet.inner_text()
    mark("machines.dormant-sheet")


def test_machine_rows_and_sheet(browser, admin_url):
    with fresh_page(browser, viewport=PHONE, has_touch=True) as page:
        _open_machines(page, admin_url, FAKE_STATUS)
        _wake_is_offered_only_to_a_down_machine_with_a_mac(page, lambda name: surface(page, name))
        _an_up_machine_opens_its_sheet(page)
        _this_machines_sheet_lists_its_health_and_resources(page)
        _wake_posts_without_a_confirm_and_toasts(page)
    with fresh_page(browser, viewport=WIDE_VIEWPORT) as page:
        _a_wide_window_fills_the_detail_pane(page, admin_url)
    with fresh_page(browser, viewport=PHONE, has_touch=True) as page:
        _sync_is_offered_only_when_it_can_help(page, admin_url, lambda name: surface(page, name))
    with fresh_page(browser, viewport=PHONE, has_touch=True) as page:
        _dormant_machines_raise_no_alert_and_sort_last(page, admin_url, lambda name: surface(page, name))
