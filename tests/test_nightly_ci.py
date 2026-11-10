"""The nightly-CI stage: READY parsing, the ci-red-main lifecycle, the flake ledger and
its 3-in-14 rule, the digest, and the unknown states. Run folders are fixtures: SSH and
``gh`` are faked, nothing reaches the Mac or GitHub."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pytest

from src.fleet import mac_results as mr
from src.fleet import mac_results_store as store
from src.fleet import nightly_ci as nc
from src.fleet import nightly_ci_issues as ni
from src.fleet.ssh_exec import SshResult

REPO = "task-os"
NOW = 10_000_000.0
DAY = 86400


def _ok(text: str) -> SshResult:
    return SshResult(returncode=0, stdout=text, stderr="")


def _b64(data: bytes) -> SshResult:
    return _ok(base64.encodebytes(data).decode())


def junit(*failed: str, passed: Sequence[str] = ("tests.t::test_ok",)) -> bytes:
    cases = "".join(f'<testcase classname="{c.split("::")[0]}" name="{c.split("::")[1]}"/>' for c in passed)
    cases += "".join(
        f'<testcase classname="{c.split("::")[0]}" name="{c.split("::")[1]}"><failure message="boom"/></testcase>' for c in failed)
    return f'<?xml version="1.0"?><testsuites><testsuite>{cases}</testsuite></testsuites>'.encode()


def ready(run: str, *, result: str = "green", sha: str = "a" * 40, unit: str = "pass", e2e: str = "pass",
          smoke: str = "pass", flaky: int = 0, repo: str = REPO) -> str:
    return (f"run={run}\nrepo={repo}\nsha={sha}\nresult={result}\nunit={unit}\ne2e={e2e}\nsmoke={smoke}\n"
            f"flaky={flaky}\naudit=clean\n")


class FakeMac:
    """Answers ``list``, ``ready`` and ``text`` from a dict of nights."""

    def __init__(self, nights: Dict[str, Dict]) -> None:
        self.nights = nights  # run id -> {repo, age, ready: str, files: {name: bytes}}
        self.commands: List[str] = []
        self.down_for: Optional[str] = None  # a verb that fails with a ssh error

    def __call__(self, command: str, timeout: int) -> SshResult:
        self.commands.append(command)
        verb, *args = command.split(" ")
        if verb == self.down_for:
            return SshResult(returncode=255, stdout="", stderr="")
        if verb == "list":
            lines = ["NOW 1000000"]
            for run, n in self.nights.items():
                lines.append(f"RUN {n.get('repo', REPO)} {run} ready {1_000_000 - n.get('age', 60)} pass pass clean")
            return _ok("\n".join(lines) + "\n")
        night = self.nights[args[1]]
        if verb == "ready":
            return _ok(night["ready"])
        if verb == "text":
            data = night.get("files", {}).get(args[2])
            return SshResult(returncode=3, stdout="", stderr="") if data is None else _b64(data)
        if verb in ("files", "audit"):
            return _ok("")
        raise AssertionError(command)


class FakeGh:
    """An in-memory GitHub: issues and comments per repo slug."""

    def __init__(self) -> None:
        self.issues: List[dict] = []
        self.calls: List[Sequence[str]] = []
        self.fail: Optional[str] = None  # a gh subcommand ("issue create") that fails

    def __call__(self, args: Sequence[str]) -> Optional[str]:
        self.calls.append(list(args))
        head = " ".join(args[:2])
        if self.fail == head:
            return None

        def opt(flag: str) -> str:
            return args[args.index(flag) + 1]

        if head == "label create":
            return ""
        if head == "issue list":
            label = opt("--label")
            return json.dumps([i for i in self.issues
                               if i["state"] == "open" and label in i["labels"] and i["repo"] == opt("--repo")])
        if head == "issue create":
            n = len(self.issues) + 1
            self.issues.append({
                "number": n, "repo": opt("--repo"), "title": opt("--title"), "body": opt("--body"), "state": "open",
                "labels": [args[i + 1] for i, a in enumerate(args) if a == "--label"], "comments": [],
                "assignee": opt("--assignee"),
            })
            return f"https://example.invalid/{n}\n"
        issue = next(i for i in self.issues if i["number"] == int(args[2]))
        if head == "issue view":
            return json.dumps({"body": issue["body"], "comments": [{"body": c} for c in issue["comments"]]})
        if head == "issue comment":
            issue["comments"].append(opt("--body"))
            return ""
        if head == "issue close":
            issue["state"] = "closed"
            issue["comments"].append(opt("--comment"))
            return ""
        raise AssertionError(args)

    def open_issues(self, label: str) -> List[dict]:
        return [i for i in self.issues if i["state"] == "open" and label in i["labels"]]

    def creates(self) -> List[Sequence[str]]:
        return [c for c in self.calls if list(c[:2]) == ["issue", "create"]]


class FakeSender:
    def __init__(self) -> None:
        self.sent: List[tuple] = []
        self.kind = "sent"

    def __call__(self, path: Path, caption: str) -> mr.SendOutcome:
        self.sent.append((path.read_text(encoding="utf-8").strip(), caption))
        return mr.SendOutcome(self.kind, "x")


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "mac_results.sqlite3")
    yield c
    c.close()


def _pass(mac, gh, sender, conn, tmp_path, **kw):
    return mr.run_pass(mac, sender, conn, tmp_path / "staging", gh=gh, now=NOW, **kw)


def _states(report) -> List[tuple]:
    return [(o.run_id, o.state) for o in report.ci_outcomes if o.run_id != "-"]


# ------------------------------------------------------------------ parsing


def test_parse_ready_reads_the_fields_and_distrusts_anything_outside_the_vocabulary():
    night = nc.parse_ready(ready("5", result="red", unit="fail", smoke="not_run", flaky=2), REPO, "5")
    assert (night.result, night.sha, night.flaky) == ("red", "a" * 40, 2)
    assert night.legs == {"unit": "fail", "e2e": "pass", "smoke": "not-run"}
    odd = nc.parse_ready("run=5\nrepo=task-os\nsha=$(id)\nresult=fine\nunit=great\n", REPO, "5")
    assert (odd.sha, odd.result, odd.legs["unit"]) == ("unknown", "unknown", "unknown")


def test_parse_ready_refuses_a_marker_naming_another_run_or_repo():
    with pytest.raises(nc.NightRefusal):
        nc.parse_ready(ready("6"), REPO, "5")
    with pytest.raises(nc.NightRefusal):
        nc.parse_ready(ready("5", repo="other"), REPO, "5")


def test_a_green_result_with_a_failed_leg_is_unknown_not_green():
    assert nc.parse_ready(ready("5", result="green", e2e="fail"), REPO, "5").result == "unknown"


def test_failed_ids_reads_failures_and_refuses_xml_entities():
    assert nc.failed_ids(junit("tests.a::test_x[1]", "tests.b::test_y")) == ["tests.a::test_x[1]", "tests.b::test_y"]
    bomb = (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><testsuite>'
            b'<testcase classname="a" name="&a;"><failure/></testcase></testsuite>')
    assert nc.failed_ids(bomb) is None
    assert nc.failed_ids(b"<not xml") is None


def test_clean_ids_drops_paths_backticks_and_caps_the_list():
    shown, withheld = nc.clean_ids(["tests.a::test_ok", "tests.a::test_ok", "/Users/runner/x.py::t", "a::`b`", "c::d\\e"])
    assert shown == ["tests.a::test_ok"] and withheld == 3
    many, left = nc.clean_ids([f"m::t{i:03d}" for i in range(80)])
    assert len(many) == nc.MAX_IDS_SHOWN and left == 30


# ----------------------------------------------------- the ci-red-main lifecycle


def _red_night(run="100", age=60):
    files = {
        "junit-retry.xml": junit("tests.e2e::test_a[webkit]"),
        "junit.xml": junit("tests.e2e::test_a[webkit]", "tests.e2e::test_b"),
    }
    return {"age": age, "ready": ready(run, result="red", e2e="fail", sha="b" * 40), "files": files}


def test_a_red_night_opens_one_labelled_issue_with_counts_and_ids_only(conn, tmp_path):
    gh, sender = FakeGh(), FakeSender()
    report = _pass(FakeMac({"100": _red_night()}), gh, sender, conn, tmp_path)
    assert _states(report) == [("100", nc.REPORTED)]
    [issue] = gh.open_issues("ci-red-main")
    assert issue["title"] == "main's nightly gate is red"
    assert issue["labels"] == ["bug", "ci-red-main"] and issue["assignee"] == "@me"
    assert "b" * 40 in issue["body"] and "Failing legs: e2e." in issue["body"]
    assert "tests.e2e::test_a[webkit]" in issue["body"] and "tests.e2e::test_b" not in issue["body"]  # the retry's set wins
    assert gh.calls[0][:3] == ["label", "create", "ci-red-main"]
    assert report.exit_code == 0


def test_a_second_pass_changes_nothing_and_a_new_sha_comments_on_the_same_issue(conn, tmp_path):
    gh, sender = FakeGh(), FakeSender()
    mac = FakeMac({"100": _red_night()})
    _pass(mac, gh, sender, conn, tmp_path)
    _pass(mac, gh, sender, conn, tmp_path)
    assert len(gh.creates()) == 1
    mac.nights["101"] = _red_night("101", age=30)
    mac.nights["101"]["ready"] = ready("101", result="red", e2e="fail", sha="c" * 40)
    _pass(mac, gh, sender, conn, tmp_path)
    assert len(gh.issues) == 1 and "c" * 40 in gh.issues[0]["comments"][0]


def test_the_next_green_night_comments_and_closes_the_issue(conn, tmp_path):
    gh, sender = FakeGh(), FakeSender()
    mac = FakeMac({"100": _red_night(age=2 * DAY)})
    _pass(mac, gh, sender, conn, tmp_path)
    assert len(gh.open_issues("ci-red-main")) == 1
    mac.nights["101"] = {"age": 60, "ready": ready("101", sha="d" * 40)}
    report = _pass(mac, gh, sender, conn, tmp_path)
    assert gh.open_issues("ci-red-main") == []
    assert "d" * 40 in gh.issues[0]["comments"][-1]
    assert dict(_states(report))["101"] == nc.REPORTED


def test_a_green_night_with_no_open_issue_files_nothing(conn, tmp_path):
    gh = FakeGh()
    _pass(FakeMac({"100": {"age": 60, "ready": ready("100")}}), gh, FakeSender(), conn, tmp_path)
    assert gh.issues == [] and gh.creates() == []


def test_nights_are_applied_oldest_first_so_the_newer_green_closes_the_older_red(conn, tmp_path):
    gh = FakeGh()
    mac = FakeMac({"101": {"age": 60, "ready": ready("101")}, "100": _red_night(age=DAY)})
    _pass(mac, gh, FakeSender(), conn, tmp_path)
    assert gh.issues[0]["state"] == "closed"


def test_a_red_leg_without_a_junit_file_still_files_and_says_ids_are_unread(conn, tmp_path):
    gh = FakeGh()
    night = {"age": 60, "ready": ready("100", result="red", unit="fail"), "files": {}}
    _pass(FakeMac({"100": night}), gh, FakeSender(), conn, tmp_path)
    assert "No failing test ids could be read" in gh.open_issues("ci-red-main")[0]["body"]


def test_ids_that_look_like_paths_never_reach_the_issue(conn, tmp_path):
    gh = FakeGh()
    night = {"age": 60, "ready": ready("100", result="red", e2e="fail"),
             "files": {"junit-retry.xml": junit("tests.a::test_x", "tests.a::test_/Users/runner/secret")}}
    _pass(FakeMac({"100": night}), gh, FakeSender(), conn, tmp_path)
    body = gh.open_issues("ci-red-main")[0]["body"]
    assert "secret" not in body and "/Users" not in body and "1 further id(s)" in body


# ------------------------------------------------------------- unknown states


def test_a_ready_without_a_result_is_unknown_and_retried_never_green(conn, tmp_path):
    gh = FakeGh()
    old = "run=100\nrepo=task-os\ne2e=pass\nsmoke=pass\naudit=clean\n"
    mac = FakeMac({"100": {"age": 60, "ready": old}})
    report = _pass(mac, gh, FakeSender(), conn, tmp_path)
    assert _states(report) == [("100", nc.UNKNOWN)] and report.exit_code == 1
    assert gh.calls == [] and not store.ci_reported(conn, REPO, "100")
    assert _states(_pass(mac, gh, FakeSender(), conn, tmp_path)) == [("100", nc.UNKNOWN)]


def test_an_unreadable_ready_and_a_failed_gh_are_not_confirmed_and_retried(conn, tmp_path):
    gh = FakeGh()
    mac = FakeMac({"100": _red_night()})
    mac.down_for = "ready"
    assert _states(_pass(mac, gh, FakeSender(), conn, tmp_path)) == [("100", nc.UNKNOWN)]
    mac.down_for = None
    gh.fail = "issue create"
    report = _pass(mac, gh, FakeSender(), conn, tmp_path)
    assert _states(report) == [("100", nc.UNKNOWN)] and report.exit_code == 1
    gh.fail = None
    assert _states(_pass(mac, gh, FakeSender(), conn, tmp_path)) == [("100", nc.REPORTED)]
    assert len(gh.open_issues("ci-red-main")) == 1


def test_a_junit_file_that_cannot_be_asked_for_is_unknown_not_filed_without_ids(conn, tmp_path):
    gh = FakeGh()
    mac = FakeMac({"100": _red_night()})
    mac.down_for = "text"
    assert _states(_pass(mac, gh, FakeSender(), conn, tmp_path)) == [("100", nc.UNKNOWN)]
    assert gh.issues == []


def test_a_ready_naming_another_run_is_refused(conn, tmp_path):
    report = _pass(FakeMac({"100": {"age": 60, "ready": ready("999")}}), FakeGh(), FakeSender(), conn, tmp_path)
    assert _states(report) == [("100", nc.REFUSED)] and report.exit_code == 1


def test_without_gh_the_stage_is_off_and_a_dry_run_only_lists(conn, tmp_path):
    mac = FakeMac({"100": _red_night()})
    assert mr.run_pass(mac, FakeSender(), conn, tmp_path / "s", now=NOW).ci_outcomes == []
    dry = _pass(mac, FakeGh(), FakeSender(), conn, tmp_path, dry_run=True)
    assert _states(dry) == [("100", nc.WOULD_REPORT)]
    assert not any(c.startswith(("ready", "text")) for c in mac.commands)


# ------------------------------------------------------- flake ledger and rule


def _flaky_night(run: str, age: int, ids=("tests.e2e::test_flaky[webkit]",), repo=REPO):
    return {"repo": repo, "age": age, "ready": ready(run, e2e="flaky", flaky=len(ids), repo=repo),
            "files": {"e2e-flaky.txt": "".join(f"{i}\n" for i in ids).encode()}}


def test_three_flaky_nights_within_fourteen_days_file_one_deduped_issue(conn, tmp_path):
    gh = FakeGh()
    mac = FakeMac({"1": _flaky_night("1", 3 * DAY), "2": _flaky_night("2", 2 * DAY)})
    _pass(mac, gh, FakeSender(), conn, tmp_path)
    assert gh.open_issues("flaky-test") == []  # two nights: not yet
    mac.nights["3"] = _flaky_night("3", DAY)
    report = _pass(mac, gh, FakeSender(), conn, tmp_path)
    [issue] = gh.open_issues("flaky-test")
    assert issue["title"] == "flaky test: tests.e2e::test_flaky[webkit]" and issue["labels"] == ["bug", "flaky-test"]
    assert "3 nightly runs within 14 days" in issue["body"]
    assert "flaky-test created" in {o.run_id: o.detail for o in report.ci_outcomes}["3"]
    mac.nights["4"] = _flaky_night("4", 60)
    _pass(mac, gh, FakeSender(), conn, tmp_path)
    assert len(gh.issues) == 1  # the open issue's marker dedupes the fourth night


def test_flakes_outside_the_fourteen_day_window_do_not_count(conn, tmp_path):
    gh = FakeGh()
    test_id = "tests.e2e::test_flaky"
    for run, age in (("1", 20 * DAY), ("2", 18 * DAY)):
        store.record_flaky(conn, REPO, run, [test_id], NOW - age)
    _pass(FakeMac({"3": _flaky_night("3", 60, ids=(test_id,))}), gh, FakeSender(), conn, tmp_path)
    assert gh.open_issues("flaky-test") == []
    assert store.flaky_night_count(conn, REPO, test_id, NOW - 14 * DAY) == 1


def test_the_ledger_counts_a_night_once_however_often_it_is_read_and_is_per_repo(conn):
    for _ in range(3):
        store.record_flaky(conn, REPO, "1", ["m::t"], NOW)
    store.record_flaky(conn, "other", "9", ["m::t"], NOW)
    assert store.flaky_night_count(conn, REPO, "m::t", 0) == 1
    assert store.flaky_night_count(conn, "other", "m::t", 0) == 1


def test_unreadable_flaky_ids_leave_the_night_unreported(conn, tmp_path):
    mac = FakeMac({"1": _flaky_night("1", 60)})
    mac.down_for = "text"
    assert _states(_pass(mac, FakeGh(), FakeSender(), conn, tmp_path)) == [("1", nc.UNKNOWN)]
    assert not store.ci_reported(conn, REPO, "1")


# --------------------------------------------------------------------- digest


def test_digest_line_counts_runs_green_red_unknown_and_flaky():
    nights = [("a", "1", "green", 0)] * 5 + [("task-os", "2", "red", 0), ("b", "3", "green", 2)]
    assert nc.digest_line(nights, []) == "Nightly CI: 7 run, 6 green, 1 red (task-os), 2 flaky"
    assert nc.digest_line([("a", "1", "green", 0)], ["x"]) == "Nightly CI: 2 run, 1 green, 1 unknown (x)"


def test_digest_goes_to_the_chat_once_and_a_failed_send_is_retried(conn, tmp_path):
    gh, sender = FakeGh(), FakeSender()
    mac = FakeMac({"1": {"age": 60, "ready": ready("1")}, "2": _red_night("2", age=30)})
    sender.kind = "unavailable"
    first = _pass(mac, gh, sender, conn, tmp_path)
    assert first.ci_outcomes[-1].state == nc.DIGEST_NOT_CONFIRMED and first.exit_code == 1
    sender.kind = "sent"
    second = _pass(mac, gh, sender, conn, tmp_path)
    assert second.ci_outcomes[-1].state == nc.DIGEST_SENT
    assert sender.sent[-1] == ("Nightly CI: 2 run, 1 green, 1 red (task-os)",) * 2
    third = _pass(mac, gh, sender, conn, tmp_path)
    assert third.ci_outcomes == [] and len(sender.sent) == 2


def test_digest_names_an_unknown_night_beside_a_reported_one(conn, tmp_path):
    mac = FakeMac({"1": {"age": 60, "ready": ready("1")},
                   "2": {"repo": "other", "age": 30, "ready": "run=2\nrepo=other\n"}})
    sender = FakeSender()
    report = _pass(mac, FakeGh(), sender, conn, tmp_path)
    assert sender.sent[0][1] == "Nightly CI: 2 run, 1 green, 1 unknown (other)"
    assert report.exit_code == 1


def test_a_pass_with_nothing_new_sends_no_digest(conn, tmp_path):
    sender = FakeSender()
    assert _pass(FakeMac({}), FakeGh(), sender, conn, tmp_path).ci_outcomes == [] and sender.sent == []


# ---------------------------------------------- a legacy folder is classified once


class LegacyAuditMac(FakeMac):
    """Lists the ``legacy_runs`` with the ``-`` audit word the dispatcher prints for a READY that predates the audit."""

    def __init__(self, nights: Dict[str, Dict], legacy_runs: Sequence[str]) -> None:
        super().__init__(nights)
        self.legacy_runs = set(legacy_runs)

    def __call__(self, command: str, timeout: int) -> SshResult:
        got = super().__call__(command, timeout)
        if command != "list":
            return got
        lines = [ln[:-len("clean")] + "-" if ln.startswith("RUN ") and ln.split(" ")[2] in self.legacy_runs else ln
                 for ln in got.stdout.splitlines()]
        return _ok("\n".join(lines) + "\n")


def test_a_legacy_folder_is_one_outcome_and_does_not_turn_the_verdict_red(conn, tmp_path, capsys):
    # A READY with no recorded nightly result reads "unknown" in the CI step; the audit step already called it legacy.
    mac = LegacyAuditMac({"100": {"age": 3 * 86400, "ready": "run=100\nrepo=task-os\n"}}, ["100"])
    report = _pass(mac, FakeGh(), FakeSender(), conn, tmp_path)
    assert [(o.run_id, o.state) for o in report.outcomes if o.state == mr.LEGACY] == [("100", mr.LEGACY)]
    assert report.ci_outcomes == [] and not any(c.startswith("ready") for c in mac.commands)
    assert report.exit_code == 0 and report.attention == 0
    mr._print_report(report)
    assert "verdict: ok - old folders only: 1 legacy" in capsys.readouterr().out


def test_an_unknown_night_beside_a_legacy_folder_still_reports_unknown_and_red(conn, tmp_path, capsys):
    nights = {"100": {"age": 3 * 86400, "ready": "run=100\nrepo=task-os\n"},
              "101": {"age": 60, "ready": "run=101\nrepo=task-os\n"}}
    report = _pass(LegacyAuditMac(nights, ["100"]), FakeGh(), FakeSender(), conn, tmp_path)
    assert [(o.run_id, o.state) for o in report.outcomes if o.state == mr.LEGACY] == [("100", mr.LEGACY)]
    assert _states(report) == [("101", nc.UNKNOWN)]
    assert report.exit_code == 1 and report.attention == 1
    mr._print_report(report)
    assert "verdict: red - 1 item(s)" in capsys.readouterr().out


# ------------------------------------------------------------------ gh helpers


def test_close_green_is_unknown_when_gh_cannot_list():
    gh = FakeGh()
    gh.fail = "issue list"
    assert ni.close_green(gh, REPO, "a" * 40)[0] == ni.UNKNOWN


def test_label_creation_is_the_forced_idempotent_call():
    gh = FakeGh()
    assert ni.ensure_label(gh, "o/r", "ci-red-main") and "--force" in gh.calls[0]
