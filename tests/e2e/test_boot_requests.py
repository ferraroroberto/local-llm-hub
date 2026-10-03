"""The Hub boot burst leaves out the live-probe endpoints (#646).

``/api/install/status`` runs the whole install battery and ``/api/fleet-placement``
probes every peer; each takes seconds and neither feeds the landing view. They load
when their card is opened (Health & install) or when the Models tab opens.
"""

from __future__ import annotations

_SLOW = ("/admin/api/install/status", "/admin/api/fleet-placement")


def test_boot_does_not_wait_on_live_probe_endpoints(page, admin_url):
    seen: list[str] = []
    page.on("request", lambda r: seen.append(r.url.split("?")[0].split("8", 1)[-1][-40:]))
    page.goto(admin_url, wait_until="load")
    page.wait_for_selector("#paneHub .home-head", state="visible", timeout=5000)
    page.wait_for_function(
        "document.getElementById('hubLiveStatusText').textContent.indexOf('checking') === -1",
        timeout=8000,
    )
    boot = [u for u in seen if any(u.endswith(s.split("/admin")[1]) for s in _SLOW)]
    assert boot == [], f"boot fetched {boot}"

    # Opening the Health & install card loads its checks.
    page.click("#installCard > summary")
    page.wait_for_function(
        "document.getElementById('installSummary').textContent.indexOf('checks') !== -1",
        timeout=15000,
    )
