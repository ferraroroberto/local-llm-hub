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
        """A row is ``(pid, port, status)`` or ``(pid, port, status, remote_port)``."""
        return [SimpleNamespace(pid=row[0], laddr=SimpleNamespace(port=row[1]), status=row[2],
                                raddr=SimpleNamespace(port=row[3]) if len(row) > 3 else ())
                for row in self.rows[protocol]]


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


def _event(record_id: int, ts: float, event_id: int) -> Dict:
    return {"record_id": record_id, "ts": ts, "event_id": event_id,
            "protocol": port_probe.EXHAUSTION_EVENTS[event_id][0],
            "reason": port_probe.EXHAUSTION_EVENTS[event_id][1]}


def test_a_lone_4227_with_low_port_use_is_watch_and_keeps_its_time(rig):
    rig.table.set(tcp=_endpoints(202, 3))
    rig.events.append(_event(10, T0 - 90, 4227))
    rig.pw.tick(_cfg(), now=T0)
    verdict = watch.WATCH.current(now=T0)
    assert verdict["verdict"] == "watch"
    (found,) = [f for f in verdict["findings"] if f["metric"] == "tcpip_4227"]
    assert found["level"] == "watch" and found["ts"] == T0 - 90
    assert "Tcpip 4227" in found["reason"]


@pytest.mark.parametrize("table", [
    {"tcp": _endpoints(101, 90)},
    {"tcp": _endpoints(202, 2) + _endpoints(303, 60, start=LO + 2, status=psutil.CONN_TIME_WAIT)},
], ids=["ports-at-act-line", "time-wait-at-act-line"])
def test_a_4227_with_port_use_at_the_act_line_is_act(rig, table):
    rig.table.set(**table)
    rig.events.append(_event(11, T0 - 90, 4227))
    cfg = _cfg(tcp_time_wait={"watch": 20, "act": 50})
    rig.pw.tick(cfg, now=T0)
    verdict = watch.WATCH.current(now=T0)
    (found,) = [f for f in verdict["findings"] if f["metric"] == "tcpip_4227"]
    assert found["level"] == "act" and verdict["verdict"] == "act"


def test_4231_and_4266_are_act_whatever_the_port_use(rig):
    rig.table.set(tcp=_endpoints(202, 3))
    rig.events.append(_event(12, T0 - 90, 4231))
    rig.events.append(_event(13, T0 - 60, 4266))
    rig.pw.tick(_cfg(), now=T0)
    verdict = watch.WATCH.current(now=T0)
    levels = {f["metric"]: f["level"] for f in verdict["findings"] if f["metric"].startswith("tcpip_")}
    assert levels == {"tcpip_4231": "act", "tcpip_4266": "act"}
    assert verdict["verdict"] == "act"


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


# --- the holder snapshot taken the moment a 4231 / 4266 lands ---

HUB_PORT, WEB_PORT = 8000, 8445
LISTENERS = [(404, HUB_PORT, psutil.CONN_LISTEN), (505, WEB_PORT, psutil.CONN_LISTEN)]


def _time_wait_to(server_port: int, count: int, start: int = LO):
    """Client sockets in TIME_WAIT toward a local server: no pid, the server port is the remote one."""
    return [(0, start + i, psutil.CONN_TIME_WAIT, server_port) for i in range(count)]


def _gate_burst(rig) -> None:
    rig.table.set(tcp=LISTENERS + _time_wait_to(HUB_PORT, 60) + _time_wait_to(WEB_PORT, 5, start=LO + 60)
                  + _endpoints(101, 3, start=LO + 70))


def test_a_new_4231_snapshots_time_wait_by_port_and_listener_at_once(rig):
    rig.events.append(_event(20, T0 - 4, 4231))
    _gate_burst(rig)
    rig.pw._probe._name_of = {404: "hub.exe", 505: "web.exe", **NAMES}.get
    rig.pw.tick(_cfg(), now=T0)
    (event,) = rig.store.events_since(T0 - 60)
    snap = event["snapshot"]
    assert snap["delay_s"] == 4.0
    assert [(g["port"], g["listener"], g["count"]) for g in snap["time_wait_groups"]] == [
        (HUB_PORT, "hub.exe", 60), (WEB_PORT, "web.exe", 5)]
    assert snap["holders"][0]["name"] == "burst.exe" and snap["holders"][0]["tcp"] == 3
    assert snap["probes"] == {"tcp": "ok", "udp": "ok"}


def test_the_verdict_shows_the_snapshot_and_its_delay(rig):
    rig.events.append(_event(21, T0 - 9, 4231))
    _gate_burst(rig)
    rig.pw._probe._name_of = {404: "hub.exe", 505: "web.exe", **NAMES}.get
    rig.pw.tick(_cfg(), now=T0)
    verdict = watch.WATCH.current(now=T0)
    (found,) = [f for f in verdict["findings"] if f["metric"] == "tcpip_4231"]
    assert "snapshot 9 s after the event" in found["reason"]
    assert f"port {HUB_PORT} (hub.exe, 60 sockets)" in found["reason"]
    assert "burst.exe" in found["reason"]
    assert "found no holders" not in found["reason"]
    assert found["snapshot"]["delay_s"] == 9.0
    assert verdict["ports"]["last_event"]["snapshot"]["delay_s"] == 9.0
    assert "hub.exe" in verdict["ports"]["last_event"]["snapshot_summary"]


def test_a_snapshot_that_found_nothing_reads_not_confirmed_with_its_delay(rig):
    rig.events.append(_event(22, T0 - 240, 4266))
    rig.table.set()  # the burst drained long before the read
    rig.pw.tick(_cfg(), now=T0)
    (found,) = [f for f in watch.WATCH.current(now=T0)["findings"] if f["metric"] == "tcpip_4266"]
    assert found["level"] == "act"
    assert "240 s after the event found no holders: not confirmed" in found["reason"]
    assert "nothing held" not in found["reason"]


def test_a_snapshot_with_an_unreadable_table_is_not_confirmed_for_it(rig):
    def half_broken(protocol):
        if protocol == "udp":
            raise psutil.AccessDenied()
        return rig.table(protocol)

    pw = port_watch.PortWatch(port_probe.PortProbe(half_broken, NAMES.get, RANGES), rig.store,
                              read_events=lambda lookback: [_event(23, T0 - 5, 4231)])
    rig.table.set(tcp=_endpoints(101, 4))
    pw.tick(_cfg(), now=T0)
    (found,) = [f for f in pw.current(now=T0)["findings"] if f["metric"] == "tcpip_4231"]
    assert "burst.exe" in found["reason"] and "udp table not read, not confirmed for it" in found["reason"]


def test_time_wait_without_a_local_listener_groups_by_remote_port_and_names_no_process(rig):
    rig.table.set(tcp=[(0, LO + i, psutil.CONN_TIME_WAIT, 443) for i in range(7)]
                  + [(0, LO + 20, psutil.CONN_TIME_WAIT)])
    snap = rig.pw._probe.snapshot(now=T0)
    assert snap["time_wait_groups"] == [{"port": 443, "listener": None, "pid": None, "count": 7}]
    assert snap["time_wait_unplaced"] == 1


def test_server_side_time_wait_groups_on_the_local_listening_port(rig):
    rig.table.set(tcp=LISTENERS + [(0, HUB_PORT, psutil.CONN_TIME_WAIT, LO + i) for i in range(3)])
    (group,) = rig.pw._probe.snapshot(now=T0)["time_wait_groups"]
    assert (group["port"], group["pid"], group["count"]) == (HUB_PORT, 404, 3)


def test_only_a_new_range_exhaustion_event_triggers_a_snapshot(rig):
    taken: List[float] = []
    real = rig.pw._probe.snapshot
    rig.pw._probe.snapshot = lambda **kw: taken.append(kw["now"]) or real(**kw)
    cfg = _cfg()
    rig.table.set(tcp=_endpoints(202, 2))
    rig.pw.tick(cfg, now=T0)
    assert taken == []  # no event
    rig.events.append(_event(30, T0 + 10, 4227))
    rig.pw.tick(cfg, now=T0 + 60)
    assert taken == []  # connection churn is not a range exhaustion
    rig.events.append(_event(31, T0 + 70, 4231))
    rig.pw.tick(cfg, now=T0 + 120)
    assert taken == [T0 + 120]
    rig.pw.tick(cfg, now=T0 + 180)  # the overlapping re-read returns the same event
    assert taken == [T0 + 120]


def test_the_snapshot_survives_a_re_read_and_a_restart(rig):
    cfg = _cfg()
    rig.events.append(_event(40, T0 - 3, 4231))
    _gate_burst(rig)
    rig.pw.tick(cfg, now=T0)
    rig.events[:] = [_event(40, T0 - 3, 4231)]  # the next read returns the event without a snapshot
    rig.pw.tick(cfg, now=T0 + 60)
    assert rig.pw.current(now=T0 + 60)["block"]["last_event"]["snapshot"]["delay_s"] == 3.0

    reborn = port_watch.PortWatch(port_probe.PortProbe(FakeTable(), NAMES.get, RANGES), rig.store,
                                  read_events=lambda lookback: [_event(40, T0 - 3, 4231)])
    reborn.load(cfg, now=T0 + 120)
    reborn.tick(cfg, now=T0 + 120)
    last = reborn.current(now=T0 + 120)["block"]["last_event"]
    assert last["snapshot"]["delay_s"] == 3.0  # not replaced by a late, drained re-snapshot


def test_a_4231_stored_before_snapshots_existed_says_the_holder_is_not_confirmed(rig):
    rig.store.add_events([_event(50, T0 - 60, 4231)], now=T0, keep_days=30)
    pw = port_watch.PortWatch(port_probe.PortProbe(FakeTable(), NAMES.get, RANGES), rig.store,
                              read_events=lambda lookback: [])
    cfg = _cfg()
    pw.load(cfg, now=T0)
    pw.tick(cfg, now=T0)
    (found,) = [f for f in pw.current(now=T0)["findings"] if f["metric"] == "tcpip_4231"]
    assert "no holder snapshot, so the holder is not confirmed" in found["reason"]
    assert "snapshot" not in found


# --- the 60 s window of per-second counts: who peaked before the burst drained ---


def _run_window(rig, seconds: int, per_second, cfg=None) -> None:
    """Tally once a second for ``seconds`` seconds ending at T0; ``per_second(age)`` sets the table, age = seconds before T0."""
    ports = (cfg or _cfg())["ports"]
    for age in range(seconds, -1, -1):
        per_second(age)
        rig.pw.record_window(ports, now=T0 - age)


def test_a_udp_burst_in_the_window_names_its_peak_holder_in_the_stored_event(rig):
    def per_second(age):
        burst = 12 <= age <= 14  # three seconds of a burst, drained well before the event is read
        rig.table.set(udp=_endpoints(202, 5) + (_endpoints(101, 85, start=LO + 10) if burst else []))

    _run_window(rig, 59, per_second)
    rig.table.set(udp=_endpoints(202, 5))  # drained when the event log is read
    rig.events.append(_event(60, T0 - 20, 4266))
    rig.pw.tick(_cfg(), now=T0)
    (event,) = rig.store.events_since(T0 - 60)
    peak = event["peak"]
    assert peak["confirmed"] is True and peak["protocol"] == "udp"
    top = peak["holders"][0]
    assert (top["name"], top["peak"], top["low"]) == ("burst.exe", 85, 0)
    assert top["at"] == T0 - 14  # the first second it reached the peak
    reason = next(f for f in rig.pw.current(now=T0)["findings"] if f["metric"] == "tcpip_4266")["reason"]
    assert "peak UDP holders" in reason and "burst.exe 85" in reason and "(from 0)" in reason
    assert "steady.exe" not in reason.split("peak UDP holders")[1]  # a flat holder is no suspect


def test_a_window_with_nothing_unusual_says_the_holder_is_not_confirmed(rig):
    _run_window(rig, 59, lambda age: rig.table.set(udp=_endpoints(202, 50)))
    rig.events.append(_event(61, T0 - 20, 4266))
    rig.pw.tick(_cfg(), now=T0)
    (event,) = rig.store.events_since(T0 - 60)
    assert event["peak"]["confirmed"] is False
    assert event["peak"]["holders"][0]["name"] == "steady.exe"  # the data stays for the reader
    reason = next(f for f in rig.pw.current(now=T0)["findings"] if f["metric"] == "tcpip_4266")["reason"]
    assert "peak holder not confirmed: no process rose by 50 or more UDP ports" in reason
    assert "can fall between two tallies" in reason


def test_an_empty_window_and_a_window_that_starts_after_the_event_are_not_confirmed(rig):
    rig.events.append(_event(62, T0 - 20, 4266))
    rig.pw.tick(_cfg(), now=T0)  # the window thread has not tallied yet
    reason = next(f for f in rig.pw.current(now=T0)["findings"] if f["metric"] == "tcpip_4266")["reason"]
    assert "held no readable UDP tally, so the peak holder is not confirmed" in reason

    def per_second(age):
        rig.table.set(udp=_endpoints(101, 85 if age < 10 else 0))

    _run_window(rig, 10, per_second)
    rig.events.append(_event(63, T0 - 40, 4266))  # the burst predates the window's first tally
    rig.pw.tick(_cfg(), now=T0 + 60)
    (late,) = [e for e in rig.store.events_since(T0 - 60) if e["record_id"] == 63]
    assert late["peak"]["covers_event"] is False and late["peak"]["confirmed"] is False
    found = [f for f in rig.pw.current(now=T0 + 60)["findings"] if f["metric"] == "tcpip_4266"]
    assert any("the window starts after the event" in f["reason"] for f in found)


def test_the_window_drops_what_aged_out_and_a_table_that_failed_is_not_a_zero(rig):
    ports = _cfg(window_s=10)["ports"]
    for age in range(30, -1, -1):
        rig.table.set(udp=_endpoints(101, 60))
        rig.pw.record_window(ports, now=T0 - age)
    peak = rig.pw._window.peaks("udp", event_ts=T0 - 5, top_n=5, min_rise=50)
    assert peak["samples"] == 11 and peak["from"] == T0 - 10

    def half_broken(protocol):
        if protocol == "udp":
            raise OSError("table read failed")
        return rig.table(protocol)

    broken = port_watch.PortWatch(port_probe.PortProbe(half_broken, NAMES.get, RANGES), rig.store,
                                  read_events=lambda lookback: [])
    broken.record_window(ports, now=T0)
    unread = broken._window.peaks("udp", event_ts=T0, top_n=5, min_rise=50)
    assert unread["samples"] == 0 and unread["unread"] == 1 and unread["confirmed"] is False


def test_a_snapshot_ranks_holders_by_the_events_own_protocol(rig):
    # tcp order: a(202) > b(101); udp order: b > a. Each event must lead with its own protocol's holder.
    rig.table.set(tcp=_endpoints(202, 30) + _endpoints(101, 10, start=LO + 40),
                  udp=_endpoints(101, 40) + _endpoints(202, 5, start=LO + 40) + _endpoints(303, 1, start=LO + 50))
    rig.events.append(_event(70, T0 - 5, 4231))
    rig.events.append(_event(71, T0 - 5, 4266))
    rig.pw.tick(_cfg(), now=T0)
    by_id = {e["event_id"]: e["snapshot"]["holders"] for e in rig.store.events_since(T0 - 60)}
    assert [h["name"] for h in by_id[4231]] == ["steady.exe", "burst.exe"]
    assert [h["name"] for h in by_id[4266]] == ["burst.exe", "steady.exe", "dns.exe"]
    assert all(h["tcp"] for h in by_id[4231])  # a UDP-only process is no TCP holder
    # The plain 30 s sample still ranks by tcp + udp (burst.exe 50 vs steady.exe 35), which is the mix the events avoid.
    assert rig.pw._probe.sample(top_n=3, now=T0)["holders"][0]["name"] == "burst.exe"
