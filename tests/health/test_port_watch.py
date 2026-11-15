"""Port watch: bursts, UDP, logged exhaustion events, holders, restart survival.

Every test drives a fake endpoint table and a synthetic clock, so a burst of
thousands of ports costs nothing and never touches the machine's real ports.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Dict, List

import psutil
import pytest

from src.health import port_probe, port_watch, settings, watch
from src.health.port_store import PortStore

T0 = 1_800_000_000.0
LO, HI = 50_000, 50_099  # a 100-port range, so a count is a percent
RANGES = {"tcp": (LO, HI), "udp": (LO, HI)}
NAMES = {101: "burst.exe", 202: "steady.exe", 303: "dns.exe"}


class FakeTable:
    """The endpoint table a test sets per tick: ``{protocol: [(pid, port, status), ...]}``."""

    def __init__(self) -> None:
        self.rows: Dict[str, List[tuple]] = {"tcp": [], "udp": []}

    def set(self, tcp=(), udp=()) -> None:
        self.rows = {"tcp": list(tcp), "udp": list(udp)}

    def __call__(self, protocol: str):
        return [SimpleNamespace(pid=pid, laddr=SimpleNamespace(port=port), status=status)
                for pid, port, status in self.rows[protocol]]


def _endpoints(pid: int, count: int, start: int = LO, status: str = psutil.CONN_ESTABLISHED):
    return [(pid, start + i, status) for i in range(count)]


def _cfg(**ports) -> Dict:
    cfg = copy.deepcopy(settings.DEFAULTS)
    cfg["ports"].update(ports)
    return cfg


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    """A port watch on a fake table and a temp store, installed as the hub's own."""
    table = FakeTable()
    events: List[Dict] = []
    store = PortStore(tmp_path / "ports.sqlite3")
    pw = port_watch.PortWatch(port_probe.PortProbe(table, NAMES.get, RANGES), store,
                              read_events=lambda lookback: list(events))
    monkeypatch.setattr(port_watch, "WATCH", pw)
    watch.WATCH.reset()
    yield SimpleNamespace(table=table, events=events, store=store, pw=pw)
    watch.WATCH.reset()


def _slow_sample(ts: float) -> Dict:
    """The 10-minute health sample: it sees no port pressure at either end of the burst."""
    return {"ts": ts, "platform": "win32", "system": {"ephemeral_ports_percent": 1.0, "tcp_time_wait": 3},
            "probes": {"tcp": "ok"}, "processes": [], "sample_ms": 1.0}


def test_a_burst_between_two_slow_samples_is_caught_and_raises_act(rig):
    cfg = _cfg()
    assert watch.WATCH.record(_slow_sample(T0), cfg)["verdict"] == "ok"
    verdicts = {}
    for i in range(1, 20):  # 30 s ticks from T0+30 to T0+570
        ts = T0 + 30 * i
        burst = i == 10  # 85% held for a single 30 s tick, gone by the next
        rig.table.set(tcp=_endpoints(101, 85 if burst else 2))
        rig.pw.tick(cfg, now=ts)
        verdicts[ts] = watch.WATCH.refresh_alerts(now=ts)
    at_burst = verdicts[T0 + 300]
    assert at_burst["verdict"] == "act"
    assert at_burst["findings"][0]["metric"] == "tcp_percent"
    assert "burst.exe" in at_burst["reason"]
    assert [a["kind"] for a in watch.WATCH.current(now=T0 + 300)["alerts"]] == ["act"]

    # The second slow sample, 4.5 min after the burst drained, still reads act with when it peaked.
    after = watch.WATCH.record(_slow_sample(T0 + 600), cfg)
    assert after["verdict"] == "act"
    assert "peaked at 85% in use" in after["reason"]
    assert after["ports"]["tcp"]["worst_hour_percent"] == 85.0
    # Without the port watch both slow samples are ok: the burst fell between them.
    assert not any(f["metric"] == "ephemeral_ports_percent" for f in after["findings"])


def test_a_udp_only_burst_raises_a_finding(rig):
    rig.table.set(tcp=_endpoints(202, 1), udp=_endpoints(303, 60))
    rig.pw.tick(_cfg(), now=T0)
    verdict = watch.WATCH.current(now=T0)
    finding = next(f for f in verdict["findings"] if f["metric"] == "udp_percent")
    assert finding["level"] == "watch"
    assert finding["process"] == "dns.exe"
    assert verdict["ports"]["udp"]["percent"] == 60.0


def test_a_logged_exhaustion_event_is_act_with_its_time_even_when_no_sample_saw_it(rig):
    rig.table.set(tcp=_endpoints(202, 2))
    rig.events.append({"record_id": 7, "ts": T0 - 120, "event_id": 4231, "protocol": "tcp",
                       "reason": port_probe.EXHAUSTION_EVENTS[4231][1]})
    rig.events.append({"record_id": 8, "ts": T0 - 60, "event_id": 4266, "protocol": "udp",
                       "reason": port_probe.EXHAUSTION_EVENTS[4266][1]})
    rig.pw.tick(_cfg(), now=T0)
    verdict = watch.WATCH.current(now=T0)
    assert verdict["verdict"] == "act"
    events = {f["metric"]: f for f in verdict["findings"] if f["metric"].startswith("tcpip_")}
    assert events["tcpip_4231"]["ts"] == T0 - 120 and events["tcpip_4231"]["level"] == "act"
    assert events["tcpip_4266"]["ts"] == T0 - 60
    assert "Tcpip 4266" in verdict["reason"]  # the newest event leads
    assert verdict["ports"]["last_event"]["event_id"] == 4266
    # An hour later the event no longer holds the verdict, but stays the last event on record.
    rig.pw.tick(_cfg(), now=T0 + 3700)
    later = rig.pw.current(now=T0 + 3700)
    assert later["findings"] == []
    assert later["block"]["last_event"]["ts"] == T0 - 60


def test_offenders_list_port_holders_by_process_with_growth(rig):
    cfg = _cfg()
    for i in range(11):  # steady.exe gains 2 ports a tick for 10 min = 240 an hour
        rig.table.set(tcp=_endpoints(202, 5 + 2 * i) + _endpoints(101, 3, start=LO + 60))
        rig.pw.tick(cfg, now=T0 + 60 * i)
    offenders = watch.WATCH.current(now=T0 + 600)["offenders"]["ports"]
    assert [o["name"] for o in offenders] == ["steady.exe", "burst.exe"]
    assert offenders[0]["value"] == 25 and offenders[0]["tcp"] == 25
    assert offenders[0]["rate_per_hour"] == pytest.approx(120.0, rel=0.01)
    assert offenders[1]["rate_per_hour"] == pytest.approx(0.0)


def test_port_history_and_events_survive_a_restart(rig):
    cfg = _cfg()
    rig.events.append({"record_id": 9, "ts": T0 - 30, "event_id": 4231, "protocol": "tcp",
                       "reason": port_probe.EXHAUSTION_EVENTS[4231][1]})
    rig.table.set(tcp=_endpoints(101, 90))
    rig.pw.tick(cfg, now=T0)

    reborn = port_watch.PortWatch(port_probe.PortProbe(FakeTable(), NAMES.get, RANGES), rig.store,
                                  read_events=lambda lookback: [])
    assert reborn.load(cfg, now=T0 + 60) == 1
    state = reborn.current(now=T0 + 60)
    assert state["block"]["tcp"]["worst_hour_percent"] == 90.0
    assert state["block"]["last_event"]["event_id"] == 4231
    assert {f["metric"] for f in state["findings"]} >= {"tcpip_4231", "tcp_percent"}


def test_a_stale_or_failed_port_probe_is_unknown_never_fine(rig):
    cfg = _cfg()
    rig.table.set(tcp=_endpoints(202, 2))
    rig.pw.tick(cfg, now=T0)
    stale = rig.pw.current(now=T0 + 30 * 5)
    assert [f["level"] for f in stale["findings"]] == ["unknown"]

    def broken(protocol):
        raise OSError("table read failed")

    failing = port_watch.PortWatch(port_probe.PortProbe(broken, NAMES.get, RANGES), rig.store,
                                   read_events=lambda lookback: None)
    failing.tick(cfg, now=T0)
    metrics = {f["metric"] for f in failing.current(now=T0)["findings"] if f["level"] == "unknown"}
    assert metrics == {"tcp", "udp", "port_events"}


def test_the_event_log_is_read_on_its_own_cadence_with_an_overlapping_window(rig):
    reads: List[float] = []
    pw = port_watch.PortWatch(port_probe.PortProbe(rig.table, NAMES.get, RANGES), rig.store,
                              read_events=lambda lookback: reads.append(lookback) or [])
    cfg = _cfg()
    for i in range(5):
        pw.tick(cfg, now=T0 + 30 * i)
    assert reads == [24 * 3600.0, 120.0, 120.0]


def test_time_wait_counts_ownerless_sockets_and_names_no_process(rig):
    rig.table.set(tcp=_endpoints(0, 100, status=psutil.CONN_TIME_WAIT))
    rig.pw.tick(_cfg(tcp_time_wait={"watch": 50, "act": 90}), now=T0)
    findings = {f["metric"]: f for f in watch.WATCH.current(now=T0)["findings"]}
    assert findings["tcp_time_wait"]["level"] == "act"
    assert findings["tcp_percent"]["process"] is None
    assert watch.WATCH.current(now=T0)["offenders"]["ports"] == []


def test_exhaustion_events_parse_from_the_system_log_xml():
    xml = (
        "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System>"
        "<Provider Name='Tcpip'/><EventID Qualifiers='32768'>4231</EventID>"
        "<TimeCreated SystemTime='2026-11-15T00:42:27.0919579Z'/><EventRecordID>94253</EventRecordID>"
        "</System></Event>"
    )
    (event,) = port_probe._parse_events(xml)
    assert event["record_id"] == 94253 and event["event_id"] == 4231 and event["protocol"] == "tcp"
    assert event["ts"] == pytest.approx(datetime(2026, 11, 15, 0, 42, 27, 91958, tzinfo=timezone.utc).timestamp())


def test_disabled_port_watch_never_samples(rig, monkeypatch):
    cfg = _cfg(enabled=False)
    monkeypatch.setattr(settings, "load", lambda path=None: cfg)
    monkeypatch.setattr(rig.pw, "tick", lambda *a, **k: pytest.fail("sampled while disabled"))
    asyncio.run(port_watch.run_loop())
    assert rig.pw.current() is None
