"""Unit tests for the Machines console (#309) — src/machine_console.py +
app_web/routers/machines.py.

Exercises the pure capability/inventory logic (host enrollment, which
actions each machine may offer, RDP generation) and the router's contract:
the status/self endpoint shapes, the RDP download, and the destructive
power-action guard (the active hub host and SSH-less peers are refused).
Network/SSH-touching probes are monkeypatched so the suite stays hermetic.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from src import machine_console as mc
from src import server as server_mod
from src.host_profile import HostProfile, all_hosts, get_host, resolve


def _client() -> TestClient:
    return TestClient(server_mod.app)


def _run(coro):
    """Run a coroutine on a fresh worker-thread loop (see test_services_router)."""
    import threading

    bucket: dict = {}

    def _worker() -> None:
        loop = asyncio.new_event_loop()
        try:
            bucket["value"] = loop.run_until_complete(coro)
        except BaseException as exc:  # noqa: BLE001 — re-raised in caller
            bucket["error"] = exc
        finally:
            loop.close()

    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    if "error" in bucket:
        raise bucket["error"]
    return bucket.get("value")


# ------------------------------------------------------------ inventory / config


def test_all_hosts_enrolls_managed_machines():
    ids = {h.id for h in all_hosts()}
    assert {"pc-cuda", "mac-mini-m4", "openclaw", "gaming"} <= ids


def test_openclaw_has_ssh_and_rdp():
    h = get_host("openclaw")
    assert h is not None
    assert h.can_ssh is True  # address + ssh_user
    assert h.rdp and h.rdp["address"] == "192.168.0.239"
    assert h.tailscale == "laptop.tail1121fd.ts.net"
    assert h.dormant is False


def test_gaming_is_live_ssh_host():
    """The old dormant `tower` node is now `gaming`, a live Linux satellite
    (#323): SSH power actions on, and the `tower` Tailscale alias kept for the
    pending reinstall."""
    h = get_host("gaming")
    assert h is not None
    assert h.dormant is False
    assert h.can_ssh is True  # address + ssh_user → power actions
    assert h.rdp and h.rdp["address"] == "192.168.0.16"
    assert h.tailscale == "tower.tail1121fd.ts.net"  # reserved alias kept


def test_tower_host_id_is_gone():
    """The rename is complete — nothing should still resolve the old id."""
    assert get_host("tower") is None


# ------------------------------------------------------------ actions capability


def test_actions_host_offers_nothing():
    host = get_host(resolve().id)
    acts = mc._actions_for(host, is_host=True)
    assert acts == {"reboot": False, "shutdown": False, "rdp": False, "ssh_terminal": False}


def test_actions_ssh_peer_offers_power_and_terminal():
    acts = mc._actions_for(get_host("mac-mini-m4"), is_host=False)
    assert acts["reboot"] and acts["shutdown"] and acts["ssh_terminal"]


def test_actions_gaming_offers_power_terminal_and_rdp():
    acts = mc._actions_for(get_host("gaming"), is_host=False)
    assert acts == {"reboot": True, "shutdown": True, "ssh_terminal": True, "rdp": True}


# ------------------------------------------------------------------------ RDP


def test_rdp_file_generated_for_peer_with_target():
    generated = mc.rdp_file("openclaw")
    assert generated is not None
    filename, content = generated
    assert filename == "openclaw.rdp"
    assert "full address:s:192.168.0.239" in content
    assert "username:s:openclaw" in content


def test_rdp_file_none_for_host_without_target():
    assert mc.rdp_file(resolve().id) is None
    assert mc.rdp_file("does-not-exist") is None


# --------------------------------------------------------------- self snapshot


def test_self_snapshot_shape(monkeypatch):
    monkeypatch.setattr(mc.system_stats, "gpu_stats", lambda: [])  # skip nvidia-smi
    snap = _run(mc.self_snapshot())
    for key in ("cpu", "ram", "gpus", "disk", "uptime_seconds"):
        assert key in snap, snap
    assert isinstance(snap["uptime_seconds"], float)
    assert "version" not in snap  # build identity is not shown on machine cards


# ------------------------------------------------------- remote stats parsing


def test_remote_stats_parse_linux():
    from src import remote_stats

    raw = (
        "uptime 70254\ncpu 2\nmem_total_mb 15800\nmem_used_mb 1239\n"
        "disk_total_kb 95536548\ndisk_used_kb 40927072\n"
        "gpu_name NVIDIA GeForce MX250\ngpu_used_mb 4\ngpu_total_mb 2048\ngpu_util 0\n"
    )
    s = remote_stats._parse(raw)
    assert s["uptime_seconds"] == 70254
    assert s["cpu"] == {"percent": 2.0}
    assert s["ram"]["total_gb"] == 15.43 and s["ram"]["percent"] == 7.8
    assert s["disk"]["percent"] == 42.8
    assert s["gpus"][0]["name"] == "NVIDIA GeForce MX250"
    assert s["gpus"][0]["total_mb"] == 2048.0 and s["gpus"][0]["vram_percent"] == 0.2


def test_remote_stats_parse_darwin_no_gpu():
    from src import remote_stats

    raw = "uptime 1636838\ncpu 2.69\nmem_total_mb 16384\nmem_used_mb 8983\ndisk_total_kb 239362496\ndisk_used_kb 17383264\n"
    s = remote_stats._parse(raw)
    assert s["cpu"] == {"percent": 2.69}
    assert s["ram"]["percent"] == 54.8
    assert s["gpus"] == []  # macOS has no nvidia-smi — no GPU gauge


def test_remote_stats_reachable_false_without_address():
    from src import remote_stats

    # A host with no LAN address is not reachable via the TCP probe. Every
    # enrolled host now has an address, so this uses a synthetic address-less
    # profile to keep the guard covered (#323).
    addressless = HostProfile(id="nowhere", platform="linux", enabled=[])
    assert remote_stats.reachable(addressless) is False


# --------------------------------------------------------------------- endpoints


def test_machines_status_endpoint_shape(monkeypatch):
    async def _canned():
        return {
            "active_id": "pc-cuda",
            "machines": [{"id": "pc-cuda", "state": "self", "actions": {}}],
        }

    monkeypatch.setattr(mc, "machines_status", _canned)
    r = _client().get("/admin/api/machines/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "active_id" in body and isinstance(body["machines"], list)


def test_machines_self_endpoint_shape(monkeypatch):
    monkeypatch.setattr(mc.system_stats, "gpu_stats", lambda: [])
    r = _client().get("/admin/api/machines/self")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "cpu" in body and "disk" in body and "uptime_seconds" in body


def test_rdp_endpoint_downloads_for_peer():
    r = _client().get("/admin/api/machines/openclaw/rdp")
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers.get("content-disposition", "")
    assert "full address:s:192.168.0.239" in r.text


def test_rdp_endpoint_404_for_host():
    r = _client().get(f"/admin/api/machines/{resolve().id}/rdp")
    assert r.status_code == 404, r.text


# ------------------------------------------------------------ power-action guard


def test_reboot_refuses_active_host():
    r = _client().post(f"/admin/api/machines/{resolve().id}/reboot")
    assert r.status_code == 400, r.text
    assert "hub host" in r.json()["detail"]


def test_reboot_404_unknown_machine():
    r = _client().post("/admin/api/machines/nope/reboot")
    assert r.status_code == 404, r.text


def test_shutdown_refuses_ssh_less_peer(monkeypatch):
    # A peer with an rdp target but no ssh_user has no power channel. No real
    # host is SSH-less anymore (#323), so inject a synthetic one at the router's
    # host lookup.
    import app_web.routers.machines as machines_router

    ghost = HostProfile(id="ghost", platform="linux", enabled=[],
                        rdp={"address": "10.0.0.9", "user": "x"})
    monkeypatch.setattr(machines_router, "get_host",
                        lambda hid: ghost if hid == "ghost" else get_host(hid))
    r = _client().post("/admin/api/machines/ghost/shutdown")
    assert r.status_code == 400, r.text
    assert "SSH" in r.json()["detail"]


def test_reboot_success_path(monkeypatch):
    from src import remote_bootstrap

    async def _ok(host_id):
        return {"ok": True, "detail": f"reboot scheduled on {host_id}"}

    monkeypatch.setattr(remote_bootstrap, "reboot_host", _ok)
    r = _client().post("/admin/api/machines/mac-mini-m4/reboot")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_reboot_failure_bubbles_502(monkeypatch):
    from src import remote_bootstrap

    async def _fail(host_id):
        return {"ok": False, "detail": "ssh unreachable"}

    monkeypatch.setattr(remote_bootstrap, "reboot_host", _fail)
    r = _client().post("/admin/api/machines/mac-mini-m4/reboot")
    assert r.status_code == 502, r.text


# ------------------------------------------------------------- terminal status


# ---------------------------------------------------- general-SSH power channel


class _FakeCompleted:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


def test_power_command_uses_general_ssh_not_forced_command_key(monkeypatch):
    """reboot/shutdown must ride the hub user's own SSH (#311) — no ``-i``
    forced-command key, and the real ``sudo shutdown`` verb, targeting
    ``ssh_user@address`` of the peer."""
    from src import remote_bootstrap

    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted(returncode=0)

    monkeypatch.setattr(remote_bootstrap.subprocess, "run", _fake_run)
    result = remote_bootstrap._run_power_command("mac-mini-m4", "-r")

    assert result["ok"] is True
    cmd = captured["cmd"]
    assert cmd[0] == "ssh"
    assert "-i" not in cmd  # NOT the LOCAL_LLM_HUB_SSH_KEY forced-command key
    assert "roberto@192.168.0.14" in cmd  # peer ssh_user@address
    remote = cmd[-1]
    assert "sudo -n /sbin/shutdown -r now" in remote
    assert "nohup" in remote  # detached so the SSH command returns cleanly


def test_power_command_flags_map_reboot_and_shutdown(monkeypatch):
    """``reboot_host`` sends ``-r``; ``shutdown_host`` sends ``-h``."""
    from src import remote_bootstrap

    seen = []

    def _fake_run(cmd, **kwargs):
        seen.append(cmd[-1])
        return _FakeCompleted(returncode=0)

    monkeypatch.setattr(remote_bootstrap.subprocess, "run", _fake_run)
    r1 = _run(remote_bootstrap.reboot_host("openclaw"))
    r2 = _run(remote_bootstrap.shutdown_host("openclaw"))

    assert r1["ok"] and "reboot scheduled on openclaw" in r1["detail"]
    assert r2["ok"] and "shutdown scheduled on openclaw" in r2["detail"]
    assert "sudo -n /sbin/shutdown -r now" in seen[0]
    assert "sudo -n /sbin/shutdown -h now" in seen[1]


def test_power_command_guards_missing_ssh_target(monkeypatch):
    """A host with no address/ssh_user (here, an unknown id) is rejected before
    any ssh call — the router guards at the endpoint, but the layer guards
    itself too."""
    from src import remote_bootstrap

    def _boom(cmd, **kwargs):  # pragma: no cover — must never run
        raise AssertionError("ssh should not be invoked for an unroutable host")

    monkeypatch.setattr(remote_bootstrap.subprocess, "run", _boom)
    result = remote_bootstrap._run_power_command("no-such-host", "-r")
    assert result["ok"] is False
    assert "address/ssh_user" in result["error"]


def test_power_command_ssh_failure_surfaces_error(monkeypatch):
    """A non-zero ssh exit is reported, not swallowed."""
    from src import remote_bootstrap

    def _fake_run(cmd, **kwargs):
        return _FakeCompleted(returncode=255, stderr="Connection refused")

    monkeypatch.setattr(remote_bootstrap.subprocess, "run", _fake_run)
    result = remote_bootstrap._run_power_command("mac-mini-m4", "-h")
    assert result["ok"] is False
    assert "ssh exit 255" in result["error"] and "Connection refused" in result["error"]


def test_terminal_status_endpoint_shape(monkeypatch):
    from src import ssh_terminal

    async def _canned():
        return {"available": False, "reason": "not reachable", "session_host": "http://127.0.0.1:8446"}

    monkeypatch.setattr(ssh_terminal, "terminal_status", _canned)
    r = _client().get("/admin/api/machines/terminal/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"available", "reason", "session_host"}
