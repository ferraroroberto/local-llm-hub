"""The Mac results puller: listing and skip logic, the hostile-folder gates, and
every not-confirmed state, with SSH and ``send_file.py`` faked."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from src.fleet import mac_results as mr
from src.fleet import mac_results_store as store
from src.fleet.ssh_exec import SshResult

PNG = mr._PNG_MAGIC + b"\x00" * 24
NOW = 1_000_000


def _ok(stdout: str) -> SshResult:
    return SshResult(returncode=0, stdout=stdout, stderr="")


class FakeMac:
    """Answers the three verbs from a dict of runs; records every command."""

    def __init__(self, runs: Dict[str, Dict]) -> None:
        self.runs = runs  # run_id -> {ready, age, e2e, smoke, files: {rel: bytes}, kind: {rel: type}}
        self.commands: List[str] = []
        self.down: Optional[SshResult] = None
        self.fail_get: Optional[SshResult] = None
        self.corrupt_get = False

    def __call__(self, command: str, timeout: int) -> SshResult:
        self.commands.append(command)
        if self.down is not None:
            return self.down
        verb, *args = command.split(" ")
        if verb == "list":
            lines = [f"NOW {NOW}"]
            for run_id, run in self.runs.items():
                kind = "ready" if run.get("ready", True) else "pending"
                lines.append(f"RUN home-automation {run_id} {kind} {NOW - run.get('age', 60)} {run.get('e2e', 'success')} {run.get('smoke', 'success')}")
            return _ok("\n".join(lines) + "\n")
        run = self.runs[args[1]]
        if verb == "files":
            return _ok("".join(
                f"{run.get('kind', {}).get(rel, mr._REGULAR)}|{len(data)}|{rel}\n" for rel, data in run["files"].items()
            ))
        if verb == "get":
            if self.fail_get is not None:
                return self.fail_get
            data = run["files"][args[2]]
            if self.corrupt_get:
                data = data[:-3]
            return _ok(base64.encodebytes(data).decode())
        raise AssertionError(f"unexpected command {command!r}")


class FakeSender:
    def __init__(self, *outcomes: mr.SendOutcome) -> None:
        self.outcomes = list(outcomes)
        self.sent: List[tuple] = []

    def __call__(self, path: Path, caption: str) -> mr.SendOutcome:
        assert path.read_bytes().startswith(mr._PNG_MAGIC)
        self.sent.append((path.name, caption))
        return self.outcomes.pop(0) if self.outcomes else mr.SendOutcome("sent", "SENT=abc")


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "mac_results.sqlite3")
    yield c
    c.close()


def _run(files=None, **extra) -> Dict:
    return {"files": files if files is not None else {"ios/home.png": PNG, "e2e/t1/test-failed-1.png": PNG}, **extra}


def _pass(mac, sender, conn, tmp_path, **kw):
    return mr.run_pass(mac, sender, conn, tmp_path / "staging", **kw)


# ------------------------------------------------------- listing and skip logic


def test_delivers_a_new_run_once_and_a_second_pass_sends_nothing(conn, tmp_path):
    mac = FakeMac({"100": _run()})
    sender = FakeSender()
    first = _pass(mac, sender, conn, tmp_path)
    assert [(o.run_id, o.state) for o in first.outcomes] == [("100", mr.DELIVERED)]
    assert len(sender.sent) == 2
    assert store.run_delivered(conn, "home-automation", "100")

    mac.commands.clear()
    second = _pass(mac, sender, conn, tmp_path)
    assert second.outcomes == [] and second.already_delivered == 1
    assert len(sender.sent) == 2  # nothing new
    assert mac.commands == ["list"]  # not even a files/get round trip
    assert second.exit_code == 0


def test_caption_carries_repo_run_leg_and_outcome(conn, tmp_path):
    sender = FakeSender()
    _pass(FakeMac({"100": _run(e2e="failure", smoke="success")}), sender, conn, tmp_path)
    captions = [c for _, c in sender.sent]
    assert captions == [
        "home-automation · run 100 · e2e failure · 1/2",
        "home-automation · run 100 · ios success · 2/2",
    ]


def test_run_without_ready_is_pending_not_delivered(conn, tmp_path):
    mac = FakeMac({"100": _run(ready=False)})
    sender = FakeSender()
    report = _pass(mac, sender, conn, tmp_path)
    assert [(o.state, o.detail) for o in report.outcomes] == [(mr.PENDING, "no READY yet")]
    assert sender.sent == [] and mac.commands == ["list"]
    assert not store.run_delivered(conn, "home-automation", "100")
    assert report.exit_code == 0  # waiting is not a failure


def test_run_outside_the_window_is_ignored(conn, tmp_path):
    mac = FakeMac({"100": _run(age=5 * 86400), "101": _run(age=60)})
    report = _pass(mac, FakeSender(), conn, tmp_path, window_days=4)
    assert report.out_of_window == 1
    assert [o.run_id for o in report.outcomes] == ["101"]


def test_dry_run_copies_sends_and_records_nothing(conn, tmp_path):
    mac = FakeMac({"100": _run()})
    sender = FakeSender()
    report = _pass(mac, sender, conn, tmp_path, dry_run=True)
    assert [o.state for o in report.outcomes] == [mr.WOULD_DELIVER]
    assert sender.sent == [] and mac.commands == ["list"]
    assert not store.run_delivered(conn, "home-automation", "100")


def test_ready_run_with_no_png_is_empty_and_recorded(conn, tmp_path):
    report = _pass(FakeMac({"100": _run(files={})}), FakeSender(), conn, tmp_path)
    assert [o.state for o in report.outcomes] == [mr.EMPTY]
    assert store.run_delivered(conn, "home-automation", "100")


def test_malformed_listing_lines_are_reported_not_trusted():
    runs, malformed = mr.parse_listing(
        f"NOW {NOW}\n"
        f"RUN home-automation 100 ready {NOW} success success\n"
        f"RUN ../etc 101 ready {NOW} success success\n"
        f"RUN home-automation 1x2 ready {NOW} success success\n"
        "RUN too few fields\n"
        "garbage\n"
    )
    assert [r.run_id for r in runs] == ["100"]
    assert len(malformed) == 4


def test_outcome_values_outside_the_pattern_become_unknown():
    runs, _ = mr.parse_listing(f"NOW {NOW}\nRUN home-automation 100 ready {NOW} $(rm) success\n")
    assert runs[0].e2e == "unknown" and runs[0].smoke == "success"


# ---------------------------------------------------- filename and size gates


@pytest.mark.parametrize("listing, reason", [
    ("Symbolic Link|10|ios/home.png\n", "not a regular file"),
    ("Regular File|10|ios/ho me.png\n", "outside the allowed pattern"),
    ("Regular File|10|../ios/home.png\n", "outside the allowed pattern"),
    ("Regular File|10|ios/..png\n", "outside the allowed pattern"),
    ("Regular File|10|a/b/c/d/e/f.png\n", "outside the allowed pattern"),
    (f"Regular File|{mr.MAX_FILE_BYTES + 1}|ios/home.png\n", "file cap"),
    ("Regular File|ten|ios/home.png\n", "could not be parsed"),
    ("no separators\n", "could not be parsed"),
])
def test_file_gates_refuse_the_whole_run(listing, reason):
    with pytest.raises(mr.Refusal, match=reason):
        mr.parse_files(listing)


def test_file_count_and_run_total_gates():
    many = "".join(f"Regular File|1|ios/{i}.png\n" for i in range(mr.MAX_FILES_PER_RUN + 1))
    with pytest.raises(mr.Refusal, match="more than"):
        mr.parse_files(many)
    big = "".join(f"Regular File|{mr.MAX_FILE_BYTES}|ios/{i}.png\n" for i in range(6))
    with pytest.raises(mr.Refusal, match="run cap"):
        mr.parse_files(big)


def test_hostile_run_is_refused_whole_and_nothing_is_copied_or_sent(conn, tmp_path):
    mac = FakeMac({"100": _run(kind={"ios/home.png": "Symbolic Link"})})
    sender = FakeSender()
    report = _pass(mac, sender, conn, tmp_path)
    assert [o.state for o in report.outcomes] == [mr.REFUSED]
    assert "not a regular file" in report.outcomes[0].detail
    assert sender.sent == [] and not any(c.startswith("get ") for c in mac.commands)
    assert not store.run_delivered(conn, "home-automation", "100")
    assert report.exit_code == 1


@pytest.mark.parametrize("payload, reason", [
    ("!!!not base64!!!", "valid base64"),
    (base64.b64encode(PNG[:-1]).decode(), "listed size"),
    (base64.b64encode(b"GIF89a" + b"\x00" * (len(PNG) - 6)).decode(), "not a png"),
])
def test_received_bytes_are_checked_against_the_listing(payload, reason):
    with pytest.raises(mr.Refusal, match=reason):
        mr.decode_image(payload, len(PNG))


def test_staged_names_are_flat_and_safe():
    name = mr._staged_name(3, "e2e/tests-x y/test-failed-1.png".replace(" ", "-"))
    assert "/" not in name and name.startswith("03-") and name.endswith(".png")


# --------------------------------------------------- the not-confirmed states


def test_unreachable_mac_is_its_own_state_and_records_nothing(conn, tmp_path):
    mac = FakeMac({"100": _run()})
    mac.down = SshResult(returncode=255, stdout="", stderr="ssh: connect to host 10.1.2.3 port 22: No route")
    report = _pass(mac, FakeSender(), conn, tmp_path)
    assert report.list_state == "unreachable" and report.outcomes == []
    assert "10.1.2.3" not in report.list_detail  # stderr never leaks into the report
    assert report.exit_code == 1


def test_ssh_that_never_ran_is_distinct_from_one_that_exited(conn, tmp_path):
    mac = FakeMac({})
    mac.down = SshResult(returncode=None, stdout="", stderr="", error="TimeoutExpired: x")
    assert "timed out" in _pass(mac, FakeSender(), conn, tmp_path).list_detail
    mac.down = SshResult(returncode=3, stdout="", stderr="")
    assert "exited 3" in _pass(mac, FakeSender(), conn, tmp_path).list_detail


def test_failed_copy_is_not_confirmed_and_not_delivered(conn, tmp_path):
    mac = FakeMac({"100": _run()})
    mac.fail_get = SshResult(returncode=255, stdout="", stderr="")
    sender = FakeSender()
    report = _pass(mac, sender, conn, tmp_path)
    assert [o.state for o in report.outcomes] == [mr.NOT_CONFIRMED]
    assert sender.sent == [] and not store.run_delivered(conn, "home-automation", "100")


def test_partial_copy_is_refused_and_not_delivered(conn, tmp_path):
    mac = FakeMac({"100": _run()})
    mac.corrupt_get = True
    report = _pass(mac, FakeSender(), conn, tmp_path)
    assert report.outcomes[0].state == mr.REFUSED
    assert not store.run_delivered(conn, "home-automation", "100")


@pytest.mark.parametrize("outcome", [
    mr.SendOutcome("refused", "REFUSED=out_of_root"),
    mr.SendOutcome("unavailable", "UNAVAILABLE=launcher-unreachable"),
])
def test_unconfirmed_send_leaves_the_run_undelivered_and_retries(conn, tmp_path, outcome):
    mac = FakeMac({"100": _run()})
    report = _pass(mac, FakeSender(outcome), conn, tmp_path)
    assert [o.state for o in report.outcomes] == [mr.NOT_CONFIRMED]
    assert not store.run_delivered(conn, "home-automation", "100")
    assert report.exit_code == 1

    retry_sender = FakeSender()
    retry = _pass(mac, retry_sender, conn, tmp_path)
    assert [o.state for o in retry.outcomes] == [mr.DELIVERED]
    assert len(retry_sender.sent) == 2


def test_retry_resends_only_images_that_never_landed(conn, tmp_path):
    mac = FakeMac({"100": _run()})
    first = FakeSender(mr.SendOutcome("sent", "SENT=a"), mr.SendOutcome("unavailable", "UNAVAILABLE=x"))
    _pass(mac, first, conn, tmp_path)
    assert len(first.sent) == 2 and not store.run_delivered(conn, "home-automation", "100")

    retry = FakeSender()
    _pass(mac, retry, conn, tmp_path)
    assert [name for name, _ in retry.sent] == [first.sent[1][0]]  # only the second image


def test_staging_is_removed_after_each_run(conn, tmp_path):
    _pass(FakeMac({"100": _run()}), FakeSender(), conn, tmp_path)
    assert list((tmp_path / "staging").iterdir()) == []


# ------------------------------------------------------------ send_file output


@pytest.mark.parametrize("stdout, kind", [
    ("SENT=abc123\n", "sent"),
    ("REFUSED=out_of_root: nope\n", "refused"),
    ("UNAVAILABLE=no-launcher-session (x)\n", "unavailable"),
    ("", "unavailable"),
    ("something else entirely\n", "unavailable"),
    ("SENT\n", "unavailable"),
])
def test_only_a_sent_line_counts_as_sent(stdout, kind):
    assert mr.parse_send_result(stdout).kind == kind
