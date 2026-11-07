"""The GitHub Actions self-hosted runner on a fleet machine: state and verbs.

A machine whose overlay row (``config/machines.local.yaml``) carries an
``actions_runner:`` block shows a Runner section on its Machines card. Two
sources are read and reported *separately*, because they fail separately:

  * the **service** — ``./svc.sh status`` in the runner folder, over the hub
    user's own general SSH (``ssh_exec.run_ssh``), plus the boot facts that
    decide whether it comes back after a reboot (auto-login, FileVault, sleep);
  * **GitHub's view** — ``gh api`` from this machine: the runner online or
    offline and busy or idle, and the latest result of one workflow.

A source that cannot be read is ``unknown``, never folded into a fine state.
Start, Stop and Install run ``svc.sh`` over the same SSH, then re-read both
sources until they agree with the verb or a deadline passes, and report what
the re-read saw.

The runner's repo and name are read from the runner folder's own ``.runner``
file at run time, so the overlay needs only the folder (or nothing but an empty
mapping); ``repo``, ``name`` and ``workflow`` are optional overrides. None of
it is committed: this repo is public.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..host_profile import HostProfile, get_host, resolve
from ..no_window import NO_WINDOW
from . import remote_stats
from .ssh_exec import SshResult, run_ssh

logger = logging.getLogger(__name__)

DEFAULT_DIR = "~/actions-runner"
VERBS = ("start", "stop", "install")

# ssh connect budget plus the longest remote command we run (a launchctl load).
_SSH_STATUS_TIMEOUT_S = 25
_SSH_VERB_TIMEOUT_S = 45
_GH_TIMEOUT_S = 15
# GitHub marks a runner offline within seconds of its listener stopping and
# online within seconds of connecting; past this the verb reports "not yet".
_CONVERGE_DEADLINE_S = 40.0
_CONVERGE_INTERVAL_S = 3.0
# One poll of the card reads GitHub at most this often: the runners endpoint
# shares the owner's REST rate limit with everything else using their login.
_GH_CACHE_TTL_S = 15.0

_DIR_RE = re.compile(r"^(~/)?[A-Za-z0-9_.][A-Za-z0-9_./-]*$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_WORKFLOW_RE = re.compile(r"^[A-Za-z0-9_.-]+\.ya?ml$")

SERVICE_RUNNING = "running"
SERVICE_STOPPED = "stopped"
SERVICE_LOADED = "loaded"  # registered with launchd but no process (crashed or exited)
SERVICE_NOT_INSTALLED = "not_installed"
SERVICE_UNKNOWN = "unknown"

_locks: Dict[str, asyncio.Lock] = {}
_gh_cache: Dict[str, Tuple[float, Any]] = {}


class RunnerRefused(Exception):
    """A verb the current state does not allow; ``status`` is the HTTP code."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class RunnerSpec:
    directory: str
    repo: Optional[str]
    name: Optional[str]
    workflow: Optional[str]
    problem: Optional[str] = None  # set when the overlay block is unusable


def spec_for(host: HostProfile) -> Optional[RunnerSpec]:
    """The host's runner block as a validated spec, or ``None`` when it has
    none. A malformed value carries ``problem`` instead of raising, so the
    card says what is wrong rather than disappearing."""
    block = host.actions_runner
    if block is None:
        return None
    if not isinstance(block, dict):
        return RunnerSpec(DEFAULT_DIR, None, None, None, problem="actions_runner is not a mapping")
    directory = str(block.get("dir") or DEFAULT_DIR)
    repo, name, workflow = block.get("repo"), block.get("name"), block.get("workflow")
    if ".." in directory or not _DIR_RE.match(directory):
        return RunnerSpec(DEFAULT_DIR, None, None, None, problem="runner dir is not a plain path")
    for value, pattern, label in ((repo, _REPO_RE, "repo"), (name, _NAME_RE, "name"), (workflow, _WORKFLOW_RE, "workflow")):
        if value is not None and not pattern.match(str(value)):
            return RunnerSpec(directory, None, None, None, problem=f"runner {label} is not valid")
    return RunnerSpec(
        directory,
        str(repo) if repo else None,
        str(name) if name else None,
        str(workflow) if workflow else None,
    )


# ------------------------------------------------------------ remote reading

_MARKS = ("RUNNER", "SVC", "SVCRC", "AUTOLOGIN", "FILEVAULT", "PMSET")


def _status_command(directory: str) -> str:
    """One SSH round trip that prints each reading under an ``@@NAME`` marker.
    Every reading degrades alone: a missing tool leaves its section empty."""
    return (
        f"cd {directory} 2>/dev/null || {{ echo '@@NODIR'; exit 0; }}; "
        "echo '@@RUNNER'; cat .runner 2>/dev/null; echo; "
        "echo '@@SVC'; ./svc.sh status 2>&1; echo \"@@SVCRC $?\"; "
        "echo '@@AUTOLOGIN'; defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser 2>&1; "
        "echo '@@FILEVAULT'; fdesetup status 2>&1; "
        "echo '@@PMSET'; pmset -g 2>&1"
    )


def _sections(text: str) -> Dict[str, str]:
    out: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        mark = re.match(r"@@(\w+)\s*(.*)$", line)
        if mark:
            current = mark.group(1)
            out[current] = [mark.group(2)] if mark.group(2) else []
        elif current is not None:
            out[current].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def _parse_service(sections: Dict[str, str]) -> Dict[str, Any]:
    text = sections.get("SVC", "")
    rc = sections.get("SVCRC", "")
    low = text.lower()
    if "not installed" in low:
        return {"state": SERVICE_NOT_INSTALLED, "reason": "The service is not installed."}
    started = re.search(r"Started:\s*\n\s*(\S+)\s+(\S+)\s+actions\.runner", text)
    if started:
        pid, last_exit = started.group(1), started.group(2)
        if pid.isdigit() and int(pid) > 0:
            return {"state": SERVICE_RUNNING, "reason": ""}
        return {"state": SERVICE_LOADED, "reason": f"Loaded but not running (last exit {last_exit})."}
    if re.search(r"^\s*Stopped\s*$", text, re.MULTILINE):
        return {"state": SERVICE_STOPPED, "reason": ""}
    return {"state": SERVICE_UNKNOWN, "reason": f"svc.sh status was not understood (exit {rc or '?'})."}


def _parse_runner_file(raw: str) -> Dict[str, Optional[str]]:
    """``name`` and ``repo`` from the runner folder's ``.runner`` JSON (it
    starts with a BOM)."""
    try:
        data = json.loads(raw.lstrip("﻿"))
        url = str(data.get("gitHubUrl") or "")
        repo = url.split("github.com/", 1)[1].strip("/") if "github.com/" in url else None
        name = data.get("agentName")
        return {
            "name": str(name) if name and _NAME_RE.match(str(name)) else None,
            "repo": repo if repo and _REPO_RE.match(repo) else None,
        }
    except (ValueError, AttributeError):
        return {"name": None, "repo": None}


def _parse_boot(sections: Dict[str, str], ssh_user: Optional[str]) -> Dict[str, Any]:
    """Whether the runner comes back after a reboot. The runner is a LaunchAgent
    (the iOS Simulator needs the user's GUI session), so it needs that user
    auto-logged-in, FileVault off and the Mac not asleep. Each is read alone;
    ``None`` is not confirmed."""
    auto_raw = sections.get("AUTOLOGIN", "")
    autologin: Optional[bool] = None
    if "does not exist" in auto_raw:
        autologin = False
    elif auto_raw and "\n" not in auto_raw and ssh_user:
        autologin = auto_raw == ssh_user
    fde_raw = sections.get("FILEVAULT", "")
    filevault: Optional[bool] = None
    if "FileVault is On" in fde_raw:
        filevault = True
    elif "FileVault is Off" in fde_raw:
        filevault = False
    sleep_match = re.search(r"^\s*sleep\s+(\d+)", sections.get("PMSET", ""), re.MULTILINE)
    sleep_off = (sleep_match.group(1) == "0") if sleep_match else None
    restart = re.search(r"^\s*autorestart\s+(\d+)", sections.get("PMSET", ""), re.MULTILINE)
    autorestart = (restart.group(1) == "1") if restart else None

    steps: List[str] = []
    if filevault is True:
        steps.append("FileVault is on, which blocks automatic login. Log the runner user in by hand after each reboot, or turn FileVault off.")
    elif autologin is False:
        steps.append("Turn on automatic login for the runner user at the Mac (System Settings › Users & Groups). It needs that user's password, so it cannot be done from here.")
    if sleep_off is False:
        steps.append("Stop the Mac sleeping at the Mac (System Settings › Energy › Prevent automatic sleeping when the display is off).")
    if steps:
        state = "owner_step"
    elif autologin and filevault is False and sleep_off:
        state = "ok"
    else:
        state = "unknown"
    return {
        "state": state, "autologin": autologin, "filevault": filevault,
        "sleep_disabled": sleep_off, "autorestart": autorestart, "owner_steps": steps,
    }


def _read_service(host: HostProfile, spec: RunnerSpec) -> Dict[str, Any]:
    """Blocking: the SSH half of the status. Never raises."""
    address = remote_stats.dial_address(host, wait=True)
    if not address or not host.ssh_user:
        return {"reached": False, "reason": "No SSH channel is configured for this machine."}
    result: SshResult = run_ssh(
        f"{host.ssh_user}@{address}", _status_command(spec.directory),
        timeout=_SSH_STATUS_TIMEOUT_S,
    )
    if not result.ok:
        logger.warning("⚠️ runner status over SSH failed for %s: %s", host.id, result.error or f"exit {result.returncode}")
        return {"reached": False, "reason": "The machine did not answer over SSH."}
    sections = _sections(result.stdout)
    if "NODIR" in sections:
        return {"reached": True, "dir_missing": True, "reason": "The runner folder was not found."}
    return {
        "reached": True,
        "service": _parse_service(sections),
        "runner_file": _parse_runner_file(sections.get("RUNNER", "")),
        "boot": _parse_boot(sections, host.ssh_user),
    }


# ------------------------------------------------------------- GitHub reading

def _gh_api(path: str) -> Tuple[Optional[Any], Optional[str]]:
    """Blocking ``gh api`` GET: ``(json, None)`` or ``(None, sanitized reason)``.
    The reason never carries the raw stderr, which can name the repo."""
    try:
        proc = subprocess.run(
            ["gh", "api", path], capture_output=True, text=True, encoding="utf-8",
            timeout=_GH_TIMEOUT_S, creationflags=NO_WINDOW,
        )
    except FileNotFoundError:
        return None, "gh is not installed on this machine."
    except subprocess.TimeoutExpired:
        return None, "GitHub did not answer in time."
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("⚠️ gh api failed to run: %s", exc)
        return None, "gh could not be run."
    if proc.returncode != 0:
        err = proc.stderr or ""
        logger.warning("⚠️ gh api %s exit %s: %s", path.split("?")[0], proc.returncode, err.strip()[:200])
        if "gh auth login" in err or "authentication" in err.lower():
            return None, "gh is not logged in."
        code = re.search(r"HTTP (\d{3})", err)
        return None, f"GitHub refused the request (HTTP {code.group(1)})." if code else "GitHub did not answer."
    try:
        return json.loads(proc.stdout), None
    except ValueError:
        return None, "GitHub sent an unreadable answer."


def _gh_cached(path: str, *, fresh: bool) -> Tuple[Optional[Any], Optional[str]]:
    now = time.monotonic()
    hit = _gh_cache.get(path)
    if hit and not fresh and now - hit[0] < _GH_CACHE_TTL_S:
        return hit[1], None
    data, err = _gh_api(path)
    if err is None:
        _gh_cache[path] = (now, data)
    else:
        _gh_cache.pop(path, None)
    return data, err


def _read_github(repo: Optional[str], name: Optional[str], workflow: Optional[str], *, fresh: bool) -> Dict[str, Any]:
    """Blocking: GitHub's view of the runner and the latest workflow result."""
    github: Dict[str, Any] = {"state": "unknown", "busy": None, "reason": ""}
    last_run: Optional[Dict[str, Any]] = None
    if not repo or not name:
        github["reason"] = "The runner's repo or name could not be read."
        return {"github": github, "last_run": last_run}
    data, err = _gh_cached(f"repos/{repo}/actions/runners?per_page=100", fresh=fresh)
    if err:
        github["reason"] = err
    else:
        match = next((r for r in (data or {}).get("runners", []) if r.get("name") == name), None)
        if match is None:
            github.update(state="not_registered", reason="GitHub does not list this runner.")
        else:
            github.update(state="online" if match.get("status") == "online" else "offline", busy=bool(match.get("busy")))
    if workflow:
        runs, run_err = _gh_cached(f"repos/{repo}/actions/workflows/{workflow}/runs?per_page=1", fresh=fresh)
        last_run = _summarise_run(runs, run_err)
    return {"github": github, "last_run": last_run}


def _summarise_run(runs: Optional[Any], err: Optional[str]) -> Dict[str, Any]:
    if err:
        return {"state": "unknown", "reason": err}
    items = (runs or {}).get("workflow_runs") or []
    if not items:
        return {"state": "none", "reason": "The workflow has not run yet."}
    run = items[0]
    if run.get("status") != "completed":
        return {"state": "running" if run.get("status") == "in_progress" else "queued", "at": run.get("created_at")}
    concl = run.get("conclusion")
    state = {"success": "passed", "failure": "failed", "cancelled": "cancelled", "timed_out": "failed"}.get(concl, "unknown")
    return {"state": state, "at": run.get("updated_at")}


# ------------------------------------------------------------------- compose

def _headline(service: str, github: str, busy: Optional[bool]) -> str:
    """One state for the card face, from both readings."""
    if service == SERVICE_UNKNOWN or github == "unknown":
        return "unknown"
    if service == SERVICE_RUNNING and github == "online":
        return "busy" if busy else "online"
    if service in (SERVICE_STOPPED, SERVICE_NOT_INSTALLED) and github in ("offline", "not_registered"):
        return "not_installed" if service == SERVICE_NOT_INSTALLED else "stopped"
    return "mismatch"


def _allowed(service: str, github: Dict[str, Any]) -> Tuple[Dict[str, bool], Dict[str, str]]:
    """Which verbs the current readings allow, and why the others do not."""
    can: Dict[str, bool] = {v: False for v in VERBS}
    why: Dict[str, str] = {}
    if service in (SERVICE_STOPPED, SERVICE_LOADED):
        can["start"] = True
    if service == SERVICE_NOT_INSTALLED:
        can["install"] = True
    if service in (SERVICE_RUNNING, SERVICE_LOADED):
        gstate = github["state"]
        if github.get("busy") is True:
            why["stop"] = "A job is running. Stop it once the job has finished."
        elif gstate in ("online", "offline", "not_registered"):
            can["stop"] = True
        else:
            why["stop"] = "GitHub's view could not be read, so it cannot be confirmed idle."
    return can, why


async def status(host_id: str, *, fresh: bool = False) -> Dict[str, Any]:
    """Both readings of the machine's runner, composed. Raises ``RunnerRefused``
    for a machine that has no runner block."""
    host = _require_runner_host(host_id)
    spec = spec_for(host)
    assert spec is not None  # _require_runner_host refused otherwise
    if spec.problem:
        return {"configured": True, "headline": "unknown", "problem": spec.problem,
                "service": {"state": SERVICE_UNKNOWN, "reason": spec.problem},
                "github": {"state": "unknown", "busy": None, "reason": ""},
                "last_run": None, "boot": None, "actions": {v: False for v in VERBS}, "blocked": {}}
    svc = await asyncio.to_thread(_read_service, host, spec)
    runner_file = svc.get("runner_file") or {"name": None, "repo": None}
    name = spec.name or runner_file["name"]
    repo = spec.repo or runner_file["repo"]
    gh = await asyncio.to_thread(_read_github, repo, name, spec.workflow, fresh=fresh)

    if not svc.get("reached"):
        service = {"state": SERVICE_UNKNOWN, "reason": svc["reason"]}
        boot = None
    elif svc.get("dir_missing"):
        service = {"state": SERVICE_UNKNOWN, "reason": svc["reason"]}
        boot = None
    else:
        service, boot = svc["service"], svc["boot"]
    github = gh["github"]
    can, why = _allowed(service["state"], github)
    return {
        "configured": True,
        "name": name,
        "headline": _headline(service["state"], github["state"], github.get("busy")),
        "service": service,
        "github": github,
        "last_run": gh["last_run"],
        "boot": boot,
        "actions": can,
        "blocked": why,
    }


# --------------------------------------------------------------------- verbs

def _require_runner_host(host_id: str) -> HostProfile:
    host = get_host(host_id)
    if host is None:
        raise RunnerRefused(404, f"unknown machine {host_id!r}")
    if host.actions_runner is None:
        raise RunnerRefused(404, f"{host_id!r} has no Actions runner configured")
    if host_id == resolve().id:
        raise RunnerRefused(400, "the runner on the hub's own machine is not managed from here")
    return host


def _verb_command(directory: str, verb: str) -> str:
    inner = "./svc.sh install && ./svc.sh start" if verb == "install" else f"./svc.sh {verb}"
    return f"cd {directory} && {inner} 2>&1"


def _converged(verb: str, snap: Dict[str, Any]) -> Tuple[bool, bool]:
    """``(service matches the verb, GitHub matches the verb)``."""
    service, github = snap["service"]["state"], snap["github"]["state"]
    if verb == "stop":
        return service == SERVICE_STOPPED, github == "offline"
    return service == SERVICE_RUNNING, github == "online"


def _outcome_message(verb: str, service_ok: bool, github_ok: bool, snap: Dict[str, Any]) -> str:
    past = {"start": "started", "stop": "stopped", "install": "installed and started"}[verb]
    if service_ok and github_ok:
        return f"Runner {past}. GitHub shows it {snap['github']['state']}."
    if service_ok:
        seen = snap["github"]["state"]
        word = "could not be read" if seen == "unknown" else f"still shows {seen}"
        return f"Runner {past}, but GitHub {word}. It can take a minute."
    return f"Runner did not {verb}: the service reads {snap['service']['state'].replace('_', ' ')}."


async def run_verb(host_id: str, verb: str) -> Dict[str, Any]:
    """Start, stop or install the runner service, then re-read until both
    sources agree or the deadline passes. The result says what the re-read saw."""
    if verb not in VERBS:
        raise RunnerRefused(404, f"unknown runner action {verb!r}")
    host = _require_runner_host(host_id)
    spec = spec_for(host)
    assert spec is not None
    if spec.problem:
        raise RunnerRefused(409, spec.problem)
    lock = _locks.setdefault(host_id, asyncio.Lock())
    if lock.locked():
        raise RunnerRefused(409, "another runner action is already in progress")
    async with lock:
        if not host.can_ssh:
            raise RunnerRefused(400, f"{host_id!r} has no SSH channel")
        before = await status(host_id, fresh=True)
        if not before["actions"][verb]:
            reason = before["blocked"].get(verb) or f"{verb} is not available while the service reads {before['service']['state'].replace('_', ' ')}."
            raise RunnerRefused(409, reason)
        address = await remote_stats.dial_address_async(host)
        if not address:
            raise RunnerRefused(502, "the machine did not answer over SSH")
        logger.info("🏃 runner %s on %s", verb, host_id)
        result = await asyncio.to_thread(
            run_ssh, f"{host.ssh_user}@{address}", _verb_command(spec.directory, verb),
            timeout=_SSH_VERB_TIMEOUT_S,
        )
        logger.info("🏃 runner %s on %s: exit %s %s", verb, host_id, result.returncode, (result.stdout or result.error or "").strip()[-300:])
        if not result.ok:
            return {"ok": False, "outcome": "failed", "verb": verb,
                    "message": f"Runner did not {verb}: the command failed on the machine.", "runner": before}
        deadline = time.monotonic() + _CONVERGE_DEADLINE_S
        snap = before
        while True:
            snap = await status(host_id, fresh=True)
            service_ok, github_ok = _converged(verb, snap)
            if (service_ok and github_ok) or time.monotonic() >= deadline:
                break
            await asyncio.sleep(_CONVERGE_INTERVAL_S)
        outcome = "done" if service_ok and github_ok else ("service_only" if service_ok else "failed")
        logger.info("🏃 runner %s on %s: %s (service=%s github=%s)", verb, host_id, outcome, snap["service"]["state"], snap["github"]["state"])
        return {"ok": outcome == "done", "outcome": outcome, "verb": verb,
                "message": _outcome_message(verb, service_ok, github_ok, snap), "runner": snap}
