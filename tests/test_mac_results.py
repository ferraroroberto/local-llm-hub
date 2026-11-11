"""The Mac results puller: listing and skip logic, the hostile-folder gates, and
every not-confirmed state, with SSH and ``send_file.py`` faked."""

from __future__ import annotations

import base64
import json
from contextlib import closing
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from src.fleet import mac_results as mr
from src.fleet import mac_results_store as store
from src.fleet.ssh_exec import SshResult

PNG = mr._PNG_MAGIC + b"\x00" * 24
NOW = 1_000_000
# What the launcher's Job executor stamps on a Job run's child (read by fleet-config's send_file.py).
JOB_ID_ENV = "APP_LAUNCHER_JOB_ID"
JOB_RUN_ID_ENV = "APP_LAUNCHER_JOB_RUN_ID"


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
                lines.append(f"RUN home-automation {run_id} {kind} {NOW - run.get('age', 60)} {run.get('e2e', 'success')} {run.get('smoke', 'success')} {run.get('audit', 'clean')}")
            return _ok("\n".join(lines) + "\n")
        run = self.runs[args[1]]
        if verb == "audit":
            if "audit_result" in run:
                return run["audit_result"]
            return _ok(base64.encodebytes(run["audit_json"]).decode())
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


class FakeFiler:
    """Stands in for ``routing_audit.py file``; never reaches gh. Records what it was handed."""

    def __init__(self, *outcomes: mr.FileOutcome) -> None:
        self.outcomes = list(outcomes)
        self.seen: List[bytes] = []

    def __call__(self, audit_path: Path) -> mr.FileOutcome:
        self.seen.append(audit_path.read_bytes())
        return self.outcomes.pop(0) if self.outcomes else mr.FileOutcome("created", "#7")


def _audit_json(repo: str = "home-automation", **extra) -> bytes:
    return json.dumps({"repo": repo, "outcome": "miss", "miss_nodes": ["t::a"], "tiers": {"skip": 1}, **extra}).encode()


def _miss_run(**extra) -> Dict:
    return _run(audit="miss", audit_json=_audit_json(), **extra)


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


# ------------------------------------------------------------- routing audit


def test_miss_run_files_once_and_a_repeat_pass_does_nothing(conn, tmp_path):
    mac = FakeMac({"100": _miss_run()})
    filer = FakeFiler(mr.FileOutcome("created", "#7"))
    first = _pass(mac, FakeSender(), conn, tmp_path, filer=filer)
    assert [(o.state, o.detail) for o in first.outcomes] == [(mr.DELIVERED, "2 sent now, 2 in the run"), (mr.AUDIT_FILED, "FILE=created #7")]
    assert filer.seen == [_audit_json()]
    assert store.audit_filed(conn, "home-automation", "100")

    mac.commands.clear()
    second = _pass(mac, FakeSender(), conn, tmp_path, filer=filer)
    assert second.outcomes == [] and second.already_delivered == 1
    assert mac.commands == ["list"] and len(filer.seen) == 1  # no audit copy, no second filing
    assert first.exit_code == second.exit_code == 0


def test_clean_run_files_nothing(conn, tmp_path):
    mac = FakeMac({"100": _run(audit="clean")})
    filer = FakeFiler()
    report = _pass(mac, FakeSender(), conn, tmp_path, filer=filer)
    assert [o.state for o in report.outcomes] == [mr.DELIVERED]
    assert filer.seen == [] and not any(c.startswith("audit") for c in mac.commands)


@pytest.mark.parametrize("audit", ["unknown", "not_run", "not-run", "$(rm)"])
def test_unmeasured_audit_is_reported_unknown_and_retried_never_clean(conn, tmp_path, audit):
    mac = FakeMac({"100": _run(audit=audit)})
    filer = FakeFiler()
    first = _pass(mac, FakeSender(), conn, tmp_path, filer=filer)
    assert [o.state for o in first.outcomes] == [mr.AUDIT_NOT_MEASURED, mr.DELIVERED]
    assert first.exit_code == 1
    second = _pass(mac, FakeSender(), conn, tmp_path, filer=filer)  # images are done; the audit is still unknown
    assert [o.state for o in second.outcomes] == [mr.AUDIT_NOT_MEASURED] and second.exit_code == 1
    assert filer.seen == []


def test_ready_without_an_audit_line_is_legacy_and_does_not_fail_the_pass(conn, tmp_path, capsys):
    # The dispatcher prints "-" for a READY that has no audit line: written before the audit existed.
    mac = FakeMac({"100": _run(audit="-")})
    sender = FakeSender()
    report = _pass(mac, sender, conn, tmp_path, filer=FakeFiler())
    assert [o.state for o in report.outcomes] == [mr.LEGACY, mr.DELIVERED]
    assert report.exit_code == 0 and report.attention == 0 and report.legacy == 1
    assert len(sender.sent) == 2  # the old folder's images still reach the owner
    mr._print_report(report)
    assert "verdict: ok - old folders only: 1 legacy" in capsys.readouterr().out
    again = _pass(mac, sender, conn, tmp_path, filer=FakeFiler())
    assert [o.state for o in again.outcomes] == [mr.LEGACY] and again.exit_code == 0


def test_new_run_with_unmeasured_audit_still_fails_next_to_a_legacy_folder(conn, tmp_path, capsys):
    mac = FakeMac({"100": _run(audit="-", age=3 * 86400), "101": _run(audit="unknown")})
    report = _pass(mac, FakeSender(), conn, tmp_path, filer=FakeFiler())
    assert {o.run_id: o.state for o in report.outcomes if o.state != mr.DELIVERED} == {"100": mr.LEGACY, "101": mr.AUDIT_NOT_MEASURED}
    assert report.exit_code == 1 and report.attention == 1
    mr._print_report(report)
    assert "verdict: red - 1 item(s)" in capsys.readouterr().out


def test_ready_run_with_a_measured_audit_is_ok_with_no_legacy_note(conn, tmp_path, capsys):
    report = _pass(FakeMac({"100": _run(audit="clean")}), FakeSender(), conn, tmp_path, filer=FakeFiler())
    assert report.exit_code == 0 and report.legacy == 0
    mr._print_report(report)
    assert "verdict: ok\n" in capsys.readouterr().out


def test_old_run_folder_without_ready_is_legacy_but_a_recent_one_is_pending(conn, tmp_path):
    mac = FakeMac({"100": _run(ready=False, age=mr.STALE_PENDING_S + 60), "101": _run(ready=False, age=3600)})
    report = _pass(mac, FakeSender(), conn, tmp_path)
    assert {o.run_id: o.state for o in report.outcomes} == {"100": mr.LEGACY, "101": mr.PENDING}
    assert report.exit_code == 0 and mac.commands == ["list"]


def test_dispatcher_without_the_audit_field_reads_as_unknown():
    runs, malformed = mr.parse_listing(f"NOW {NOW}\nRUN home-automation 100 ready {NOW} success success\n")
    assert malformed == [] and runs[0].audit == mr.AUDIT_UNKNOWN


@pytest.mark.parametrize("outcome", [mr.FileOutcome("unknown", "gh could not list issues"), mr.FileOutcome("none", "outcome=clean")])
def test_unconfirmed_filing_is_not_recorded_and_retried(conn, tmp_path, outcome):
    mac = FakeMac({"100": _miss_run()})
    filer = FakeFiler(outcome, mr.FileOutcome("commented", "#7"))
    first = _pass(mac, FakeSender(), conn, tmp_path, filer=filer)
    assert [o.state for o in first.outcomes] == [mr.DELIVERED, mr.AUDIT_NOT_MEASURED] and first.exit_code == 1
    assert not store.audit_filed(conn, "home-automation", "100")
    second = _pass(mac, FakeSender(), conn, tmp_path, filer=filer)
    assert [o.state for o in second.outcomes] == [mr.AUDIT_FILED] and second.exit_code == 0  # images were not resent


def test_audit_filing_does_not_wait_on_the_images(conn, tmp_path):
    filer = FakeFiler()
    report = _pass(FakeMac({"100": _miss_run(files={})}), FakeSender(), conn, tmp_path, filer=filer)
    assert [o.state for o in report.outcomes] == [mr.EMPTY, mr.AUDIT_FILED]


def test_audit_still_files_when_an_image_send_failed(conn, tmp_path):
    sender = FakeSender(mr.SendOutcome("unavailable", "UNAVAILABLE=x"))
    report = _pass(FakeMac({"100": _miss_run()}), sender, conn, tmp_path, filer=FakeFiler())
    assert [o.state for o in report.outcomes] == [mr.NOT_CONFIRMED, mr.AUDIT_FILED]


def test_failed_audit_copy_is_unknown_not_filed(conn, tmp_path):
    mac = FakeMac({"100": _miss_run(audit_result=SshResult(returncode=3, stdout="", stderr="x"))})
    filer = FakeFiler()
    report = _pass(mac, FakeSender(), conn, tmp_path, filer=filer)
    assert report.outcomes[-1].state == mr.AUDIT_NOT_MEASURED and "copying audit.json failed" in report.outcomes[-1].detail
    assert filer.seen == [] and report.exit_code == 1


def test_missing_filing_script_is_unknown_and_screenshots_still_deliver(conn, tmp_path):
    report = _pass(FakeMac({"100": _miss_run()}), FakeSender(), conn, tmp_path, filer=None)
    assert [o.state for o in report.outcomes] == [mr.DELIVERED, mr.AUDIT_NOT_MEASURED]


@pytest.mark.parametrize("payload, reason", [
    (b"not json", "not valid JSON"),
    (b"[1, 2]", "does not name the repo"),
    (_audit_json("another-repo"), "does not name the repo"),
    (b"x" * (mr.MAX_AUDIT_BYTES + 1), "cap"),
], ids=["not-json", "not-an-object", "other-repo", "over-cap"])
def test_hostile_audit_json_is_refused_before_the_filing_tool_runs(conn, tmp_path, payload, reason):
    mac = FakeMac({"100": _run(audit="miss", audit_json=payload)})
    filer = FakeFiler()
    report = _pass(mac, FakeSender(), conn, tmp_path, filer=filer)
    assert report.outcomes[-1].state == mr.REFUSED and reason in report.outcomes[-1].detail
    assert filer.seen == [] and report.exit_code == 1
    assert not store.audit_filed(conn, "home-automation", "100")


def test_invalid_base64_audit_is_refused():
    with pytest.raises(mr.Refusal, match="base64"):
        mr.decode_audit("!!!not base64!!!", "home-automation")


def test_dry_run_reports_the_filing_without_copying_or_filing(conn, tmp_path):
    mac = FakeMac({"100": _miss_run()})
    filer = FakeFiler()
    report = _pass(mac, FakeSender(), conn, tmp_path, dry_run=True, filer=filer)
    assert [o.state for o in report.outcomes] == [mr.WOULD_DELIVER, mr.WOULD_FILE]
    assert mac.commands == ["list"] and filer.seen == [] and not store.audit_filed(conn, "home-automation", "100")


def test_run_delivered_before_the_audit_field_existed_still_gets_filed(conn, tmp_path):
    store.mark_run_delivered(conn, "home-automation", "100", 2)
    filer = FakeFiler()
    report = _pass(FakeMac({"100": _miss_run()}), FakeSender(), conn, tmp_path, filer=filer)
    assert [o.state for o in report.outcomes] == [mr.AUDIT_FILED] and len(filer.seen) == 1


def test_audit_staging_is_removed(conn, tmp_path):
    _pass(FakeMac({"100": _miss_run()}), FakeSender(), conn, tmp_path, filer=FakeFiler())
    assert list((tmp_path / "staging").iterdir()) == []


@pytest.mark.parametrize("stdout, state", [
    ("FILE=created https://example.invalid/issues/7\n", "created"),
    ("FILE=commented #7\n", "commented"),
    ("FILE=unchanged #7\n", "unchanged"),
    ("FILE=none outcome=clean\n", "none"),
    ("FILE=unknown gh could not list issues\n", "unknown"),
    ("some body\nFILE=created #7\n", "created"),
    ("", "unknown"),
    ("FILE=filed\n", "unknown"),
    ("created\n", "unknown"),
])
def test_only_a_file_line_counts_as_a_filing_outcome(stdout, state):
    assert mr.parse_file_result(stdout).state == state


def test_the_filer_runs_the_tool_with_the_file_subcommand_and_reads_its_line(monkeypatch, tmp_path):
    calls = []

    class Done:
        stdout = "FILE=unchanged #7\n"

    monkeypatch.setattr(mr.subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw)) or Done())
    outcome = mr.make_routing_audit_filer(tmp_path / "routing_audit.py")(tmp_path / "audit.json")
    assert outcome.state == "unchanged"
    cmd, kw = calls[0]
    assert cmd[2:] == ["file", "--audit", str(tmp_path / "audit.json")] and kw["timeout"] == mr._FILE_TIMEOUT_S


def test_the_filer_reports_a_timeout_as_unknown(monkeypatch, tmp_path):
    def boom(cmd, **kw):
        raise mr.subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(mr.subprocess, "run", boom)
    assert mr.make_routing_audit_filer(tmp_path / "x.py")(tmp_path / "a.json").state == "unknown"


# ------------------------------------------------------- CLI: host and status file


@pytest.fixture
def cli_env(monkeypatch, tmp_path):
    # main() loads the repo's .env; this box's real one must not leak a host into the test.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv(mr.HOST_ENV, raising=False)
    monkeypatch.setenv(mr.STATUS_ENV, str(tmp_path / "out" / "status.txt"))
    monkeypatch.setenv(mr.STAGING_ENV, str(tmp_path / "staging"))
    monkeypatch.setenv("MAC_RESULTS_DB_PATH", str(tmp_path / "mac_results.sqlite3"))
    # The delivery tests never reach gh; the nightly-CI stage has its own tests (test_nightly_ci.py).
    monkeypatch.setattr(mr.nightly_ci_issues, "make_gh", lambda: None)
    return tmp_path / "out" / "status.txt"


def test_host_comes_from_the_env_when_not_passed(cli_env, monkeypatch, tmp_path, capsys):
    seen = []
    monkeypatch.setenv(mr.HOST_ENV, "the-mac")
    monkeypatch.setattr(mr, "_ssh_for", lambda host: seen.append(host) or (FakeMac({}), ""))
    sender = tmp_path / "send_file.py"
    sender.write_text("", encoding="utf-8")
    assert mr.main(["--send-file", str(sender)]) == 0
    assert seen == ["the-mac"]
    assert "nothing new to deliver" in capsys.readouterr().out
    assert cli_env.read_text() == "0\n"


def test_no_host_is_not_configured_and_the_status_file_says_so(cli_env, capsys):
    assert mr.main([]) == 2
    out = capsys.readouterr().out
    assert mr.HOST_ENV in out and "setup missing" in out
    assert cli_env.read_text() == "2\n"


def test_unconfigured_key_exits_two_with_its_reason(cli_env, monkeypatch, capsys):
    monkeypatch.setattr(mr, "_ssh_for", lambda host: (None, "KEY is not set in .env"))
    assert mr.main(["--host", "x"]) == 2
    assert "KEY is not set in .env" in capsys.readouterr().out
    assert cli_env.read_text() == "2\n"


def test_unreadable_mac_exits_one_and_records_it(cli_env, monkeypatch, tmp_path, capsys):
    mac = FakeMac({})
    mac.down = SshResult(returncode=255, stdout="", stderr="")
    monkeypatch.setattr(mr, "_ssh_for", lambda host: (mac, ""))
    sender = tmp_path / "send_file.py"
    sender.write_text("", encoding="utf-8")
    assert mr.main(["--host", "x", "--send-file", str(sender)]) == 1
    assert "not read" in capsys.readouterr().out
    assert cli_env.read_text() == "1\n"


# ------------------------------------------------------- CLI: delivery from a launcher Job


class FakeSendFile:
    """Stands in for the send_file.py subprocess: answers from the session id
    and Job identity the child actually receives, the way the launcher's
    session and Job outbox routes would."""

    def __init__(self) -> None:
        self.sessions_seen: List[Optional[str]] = []

    def __call__(self, argv, **kwargs):
        env = kwargs.get("env")
        source = env if env is not None else mr.os.environ
        sid = source.get(mr.SESSION_ENV)
        self.sessions_seen.append(sid)
        if sid or (source.get(JOB_ID_ENV) and source.get(JOB_RUN_ID_ENV)):
            line = "SENT=entry-1"
        else:
            line = "UNAVAILABLE=no-launcher-session (neither APP_LAUNCHER_SESSION_ID nor a Job run identity is set)"
        return mr.subprocess.CompletedProcess(argv, 0, stdout=line + "\n", stderr="")


@pytest.fixture
def job_cli(cli_env, monkeypatch, tmp_path):
    mac = FakeMac({"100": _run()})
    monkeypatch.setattr(mr, "_ssh_for", lambda host: (mac, ""))
    fake = FakeSendFile()
    monkeypatch.setattr(mr.subprocess, "run", fake)
    monkeypatch.delenv(JOB_ID_ENV, raising=False)
    monkeypatch.delenv(JOB_RUN_ID_ENV, raising=False)
    script = tmp_path / "send_file.py"
    script.write_text("", encoding="utf-8")
    return fake, script


def test_job_never_sends_through_an_inherited_session_id(job_cli, monkeypatch, tmp_path, capsys):
    fake, script = job_cli
    monkeypatch.setenv(mr.SESSION_ENV, "someone-elses-live-session")
    monkeypatch.setenv(mr.JOB_ARTIFACT_ENV, str(tmp_path / "artifacts"))
    assert mr.main(["--host", "x", "--send-file", str(script)]) == 1
    assert fake.sessions_seen == [None, None]  # the child never saw the inherited id
    out = capsys.readouterr().out
    assert "inherited session id ignored" in out
    with closing(store.connect(tmp_path / "mac_results.sqlite3")) as conn:
        assert not store.run_delivered(conn, "home-automation", "100")


def test_job_without_a_session_keeps_images_in_its_artifacts_and_says_so_once(job_cli, monkeypatch, tmp_path, capsys):
    fake, script = job_cli
    monkeypatch.delenv(mr.SESSION_ENV, raising=False)
    artifacts = tmp_path / "artifacts"
    monkeypatch.setenv(mr.JOB_ARTIFACT_ENV, str(artifacts))
    assert mr.main(["--host", "x", "--send-file", str(script)]) == 1
    kept = sorted(p.name for p in artifacts.iterdir())
    assert len(kept) == 2 and all(n.startswith("home-automation-100-") and n.endswith(".png") for n in kept)
    assert all((artifacts / n).read_bytes() == PNG for n in kept)
    lines = capsys.readouterr().out.splitlines()
    assert sum("not_confirmed: a launcher Job could not deliver to the chat (no session id)" in ln for ln in lines) == 1
    assert any("run 100: not_confirmed - 2 not delivered" in ln and "no-launcher-session" in ln for ln in lines)
    with closing(store.connect(tmp_path / "mac_results.sqlite3")) as conn:
        assert not store.run_delivered(conn, "home-automation", "100")
        assert not store.file_sent(conn, "home-automation", "100", "ios/home.png")  # retried by the next pass


def test_job_with_its_run_identity_delivers_through_the_job_outbox(job_cli, monkeypatch, tmp_path, capsys):
    fake, script = job_cli
    monkeypatch.setenv(mr.SESSION_ENV, "someone-elses-live-session")  # stale; must not be what delivers
    monkeypatch.setenv(JOB_ID_ENV, "mac-results")
    monkeypatch.setenv(JOB_RUN_ID_ENV, "run-1")
    artifacts = tmp_path / "artifacts"
    monkeypatch.setenv(mr.JOB_ARTIFACT_ENV, str(artifacts))
    assert mr.main(["--host", "x", "--send-file", str(script)]) == 0
    assert fake.sessions_seen == [None, None]
    assert not artifacts.exists()  # nothing kept back: both images were sent
    assert "could not deliver" not in capsys.readouterr().out
    with closing(store.connect(tmp_path / "mac_results.sqlite3")) as conn:
        assert store.run_delivered(conn, "home-automation", "100")


def test_hosted_session_outside_a_job_still_sends_with_its_own_id(job_cli, monkeypatch, tmp_path, capsys):
    fake, script = job_cli
    monkeypatch.setenv(mr.SESSION_ENV, "this-session")
    monkeypatch.delenv(mr.JOB_ARTIFACT_ENV, raising=False)
    assert mr.main(["--host", "x", "--send-file", str(script)]) == 0
    assert fake.sessions_seen == ["this-session", "this-session"]
    out = capsys.readouterr().out
    assert "run 100: delivered" in out and "launcher Job" not in out
