"""Every ``/admin/api`` route has a caller, or says who it is for.

A route nothing calls is retired-feature residue that keeps being mounted,
probed, documented and maintained. The SPA is the usual caller, so a route
whose path never appears in ``app_web/static/`` must be listed below with the
non-SPA caller that justifies it. Adding a route with no caller fails here;
deleting the last caller of a listed route fails the stale-entry check.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from app_web import server as admin_server
from app_web.server import STATIC_DIR

# path -> who calls it when the SPA does not.
NON_SPA_CALLERS = {
    "/api/healthz": "liveness probe for the tray, launchers and the bearer-gate tests",
    "/api/hub/stop": "mac launchd / systemd control scripts (docs/multi-host.md)",
    "/api/hub/restart": "tray restart recipe, mac launchd, backend_process",
    "/api/hub/requests/recent": "hub_log + peer hubs and fleet repos reading the request ring",
    "/api/hub/counters": "no known caller; hub_log only filters it from the access log (removal candidate)",
    "/api/usage/summary": "vendored usage-log router (src/usage_log.py); read over HTTP by operators",
    "/api/usage/time": "vendored usage-log router (src/usage_log.py); read over HTTP by operators",
    "/api/hub/errors/recent": "hub_log reading the error ring",
    "/api/telemetry/recent": "request-ring regression tests (the OBS ring contract)",
    "/api/machines/self": "machine_console self snapshot for peer hubs",
    "/api/fleet-maintenance": "fleet-maintenance CLI and peer hubs (docs/fleet-maintenance.md)",
    "/api/fleet-maintenance/{host_id}": "fleet-maintenance CLI and peer hubs (docs/fleet-maintenance.md)",
    "/api/fleet-placement/reconcile": "operator/CLI-triggered reconcile (docs/multi-host.md)",
    "/api/diagnostics/ingest": "external diagnostics reporters (docs/diagnostics.md)",
    # Parked reference implementation, deliberately unwired — docs/running.md,
    # "Passkey (WebAuthn) gate — parked".
    "/api/webauthn/status": "parked passkey gate (docs/running.md)",
    "/api/webauthn/enroll/window": "parked passkey gate (docs/running.md)",
    "/api/webauthn/enroll/begin": "parked passkey gate (docs/running.md)",
    "/api/webauthn/enroll/finish": "parked passkey gate (docs/running.md)",
    "/api/webauthn/auth/begin": "parked passkey gate (docs/running.md)",
    "/api/webauthn/auth/finish": "parked passkey gate (docs/running.md)",
    "/api/webauthn/devices/{device_id}": "parked passkey gate (docs/running.md)",
}


def _spa_source() -> str:
    return "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in STATIC_DIR.rglob("*")
        if p.suffix in {".js", ".html"}
    )


def _api_paths() -> set[str]:
    app = admin_server.create_app()
    return {
        r.path for r in app.routes
        if isinstance(r, APIRoute) and r.path.startswith("/api/")
    }


def _called_by_spa(path: str, spa: str) -> bool:
    # A templated segment ({id}) is concatenated in JS, so match the literal
    # prefix before it.
    return "/admin" + path.split("{")[0].rstrip("/") in spa


def test_every_admin_route_has_a_spa_caller_or_a_listed_one():
    spa = _spa_source()
    orphans = sorted(
        p for p in _api_paths() if not _called_by_spa(p, spa) and p not in NON_SPA_CALLERS
    )
    assert not orphans, (
        "no SPA caller and not in NON_SPA_CALLERS — delete the route or name its caller: "
        f"{orphans}"
    )


def test_non_spa_caller_entries_are_not_stale():
    spa = _spa_source()
    paths = _api_paths()
    stale = sorted(
        p for p in NON_SPA_CALLERS if p not in paths or _called_by_spa(p, spa)
    )
    assert not stale, f"route gone or now called by the SPA — drop from NON_SPA_CALLERS: {stale}"
