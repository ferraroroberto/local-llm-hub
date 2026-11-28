"""Replace an installed app with a fresh clone at its canonical path, keeping every replaced folder as a backup.

Vendored verbatim (manifest key ``installer_inplace``), beside ``src/installer.py`` and
``src/installer_migrate.py`` (schema: project-scaffolding ``docs/installer.md``).
``setup.bat`` copies this file, ``installer_migrate.py`` and ``installer_declaration.py`` to a
temporary folder and runs this one from there with the system python, because Windows refuses
to rename a folder that a running process, or a process's current folder, is inside.

Three entries, one swap:

* ``<app>.incoming\\setup.bat -Replace <app>`` runs from a fresh clone beside the install, so
  it works whatever version the install is: none of the old copy's code runs;
* ``<app>\\setup.bat -InPlace`` first clones the folder's ``origin`` into ``<app>.incoming``
  itself, so it needs an install new enough to have it;
* ``scripts/tools/replace_app.py <app> --root <root>`` in a project-scaffolding clone runs this
  file from the scaffold, so a fix here reaches every app without re-vendoring it.

The install is found on the machine, not assumed to be ``<root>\\<app>``. A checkout is the same
app when it shares the repository's root commit (its origin URL, parsed, when one side has no
history). The candidates are ``<root>\\<app>``, every sibling ``<app>-*`` / ``<app>.*`` that is the
same app (never a ``.bak-*`` or ``.incoming`` folder), and the checkout of whatever holds the
app's port. The live copy is the one holding the port, else the one something runs from, else
the most recently committed. A port held by anything that is not the same app is refused. A
``<root>\\<app>`` that is another app's checkout is no candidate: nothing in it is stopped, it
is renamed to its backup when the clone lands there, and the run is refused while something
runs from it.

The swap, repeatable any number of times:

1. stop every process that runs from any candidate (by PID, never by name), and refuse when
   this run was itself started from one of them;
2. rename ``<root>\\<app>``, when it exists, to ``<app>.bak-YYYYMMDD-N`` (the first free N), a
   working checkout that keeps any uncommitted edits;
3. rename the incoming clone to ``<root>\\<app>``;
4. run the moved clone's ``setup.bat -MigrateFrom`` the live copy (its backup when the live copy
   was ``<root>\\<app>``): copy, rebind, secrets, install, start, verify;
5. when the live copy was a sibling and the migration passed, rename it to its own backup name.

A failure before step 2 leaves every install where it was (start the live one again with its
own start command); a failure at step 3 renames the backup back. ``-WhatIf`` names every
candidate and which one is live, the backup names, every step and every file the migration would
copy (by name) with where that list was read, and changes no file or ref. With no clone to read it
from, it fetches the origin's default branch into the copy it would clone from.
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_migrate() -> ModuleType:
    """``installer_migrate.py`` from this file's folder, by path: ``-I`` keeps the folder off ``sys.path``."""
    path = Path(__file__).with_name("installer_migrate.py")
    spec = importlib.util.spec_from_file_location("installer_migrate", path)
    if spec is None or spec.loader is None or not path.is_file():
        raise SystemExit(f"setup.bat -InPlace/-Replace: {path.name} is missing beside {Path(__file__).name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


m = sys.modules.get("installer_migrate") or _load_migrate()

Runner = Callable[..., "subprocess.CompletedProcess[Any]"]
INCOMING_SUFFIX = ".incoming"
BACKUP_MARK = ".bak-"
# scp-style remotes: user@host:owner/name, with no scheme and no drive letter before the colon.
SCP_REMOTE = re.compile(r"^(?:[^@/\\]+@)?(?P<host>[^:/\\]{2,}):(?P<path>[^/\\].*)$")
URL_REMOTE = re.compile(r"^[a-z][a-z0-9+.-]*://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/(?P<path>.+)$", re.IGNORECASE)


def backup_path(folder: Path, today: datetime.date) -> Path:
    """``<folder>.bak-YYYYMMDD-N``, the first N from 1 that is free."""
    n = 1
    while True:
        candidate = folder.with_name(f"{folder.name}{BACKUP_MARK}{today:%Y%m%d}-{n}")
        if not os.path.lexists(candidate):
            return candidate
        n += 1


def incoming_path(folder: Path) -> Path:
    """Where ``-InPlace`` clones, and the name the playbook gives the clone ``-Replace`` runs from."""
    return folder.with_name(folder.name + INCOMING_SUFFIX)


# ---------------------------------------------------------------- the same app

def parse_remote(url: str) -> tuple[str, str] | None:
    """(host, owner/name) of a remote in any https, ssh or scp form, or ("", path) for a local one; None if empty.

    Case, slash style, a trailing slash and ``.git`` are ignored.
    """
    url = (url or "").strip()
    if not url:
        return None
    found = URL_REMOTE.match(url)
    if found is None and not re.match(r"^[A-Za-z]:[\\/]", url) and not url.lower().startswith("file:"):
        found = SCP_REMOTE.match(url)
    host, path = (found.group("host"), found.group("path")) if found else ("", url.removeprefix("file://"))
    path = path.replace("\\", "/").strip("/").casefold()
    path = path[:-4] if path.endswith(".git") else path
    return host.casefold(), path


def same_origin(a: str, b: str) -> bool:
    """Whether two remote URLs name one repository, whatever their form (https, ssh, scp, local path)."""
    left, right = parse_remote(a), parse_remote(b)
    return left is not None and left == right


def repo_name(url: str | None) -> str:
    parsed = parse_remote(url or "")
    return parsed[1].rsplit("/", 1)[-1] if parsed else ""


def root_commits(folder: Path, runner: Runner) -> frozenset[str] | None:
    """The root commits of a checkout's history; None when it has none to tell (no commit, shallow, no git)."""
    if m.git(["rev-parse", "--is-shallow-repository"], folder, runner) != "false":
        return None
    roots = m.git(["rev-list", "--max-parents=0", "HEAD"], folder, runner)
    return frozenset(roots.split()) if roots else None


@dataclass
class Checkout:
    """What tells one checkout's app: its root commits and its origin."""

    folder: Path
    roots: frozenset[str] | None
    origin: str | None


def read_checkout(folder: Path, runner: Runner) -> Checkout:
    return Checkout(folder, root_commits(folder, runner), m.git(["remote", "get-url", "origin"], folder, runner))


def same_app(it: Checkout, ref: Checkout) -> tuple[bool, str]:
    """Whether ``it`` is a copy of ``ref``'s app, and why.

    A shared root commit decides, except for a repository of its own built from the app's history (a fork or a
    "lite" copy): one whose folder carries its own origin's name while that name is not the app's.
    """
    if it.roots and ref.roots:
        if not it.roots & ref.roots:
            return False, "shares no root commit with the app"
        own, app = repo_name(it.origin), repo_name(ref.origin)
        if own and app and own != app and it.folder.name.casefold() == own:
            return False, f"a repository of its own ({own}) that shares the app's history"
        return True, "shares the app's root commit"
    if it.origin and ref.origin and same_origin(it.origin, ref.origin):
        return True, "same origin (no history to compare)"
    return False, "no shared history and not the same origin"


def _is_checkout_root(folder: Path, runner: Runner) -> bool:
    top = m.git(["rev-parse", "--show-toplevel"], folder, runner)
    return top is not None and m.inside(top, folder) and m.inside(str(folder), Path(top))


def _same_folder(a: Path, b: Path) -> bool:
    return bool(m.inside(str(a), b) and m.inside(str(b), a))


def siblings(folder: Path, incoming: Path | None) -> list[Path]:
    """Checkouts beside ``folder`` named ``<app>-*`` or ``<app>.*``, never a backup or an incoming clone."""
    name = folder.name.casefold()
    if not folder.parent.is_dir():
        return []
    found: list[Path] = []
    for entry in sorted(folder.parent.iterdir()):
        low = entry.name.casefold()
        rest = low[len(name):]
        if not low.startswith(name) or rest[:1] not in ("-", ".") or BACKUP_MARK in rest:
            continue
        if rest.startswith(INCOMING_SUFFIX) or (incoming is not None and _same_folder(entry, incoming)):
            continue
        if entry.is_dir() and os.path.lexists(entry / ".git"):
            found.append(entry)
    return found


def _commit_time(folder: Path, runner: Runner) -> int:
    stamp = m.git(["log", "-1", "--format=%ct"], folder, runner)
    return int(stamp) if stamp and stamp.isdigit() else 0


def checkout_of(path: str) -> Path | None:
    """The git checkout an absolute ``path`` lies in, walking up it as spelled; None when no level holds ``.git``."""
    if not path or not os.path.isabs(path):
        return None
    spelled = Path(os.path.normpath(path))
    for level in (spelled, *spelled.parents):
        if os.path.lexists(level / ".git"):
            return level
    return None


def proc_checkout(proc: Any) -> Path | None:
    """The checkout a process runs from: its program's, or for a python, the first path its command line names."""
    found = checkout_of(proc.exe)
    if found is None and os.path.basename(proc.exe).lower() in m.PYTHON_NAMES:
        for run in m.DRIVE_PATH.findall(proc.cmdline or ""):
            found = checkout_of(run.strip())
            if found is not None:
                break
    return found


def port_holders(snap: Any, port: int) -> list[tuple[int, Any, Path | None]]:
    """Each loopback holder of ``port``, with the nearest process up its parent chain that runs from a checkout.

    That process is the holder itself, with no checkout, when nothing in the chain runs from one.
    """
    out: list[tuple[int, Any, Path | None]] = []
    for pid in sorted(snap.holders(port)):
        owner, home = snap.procs.get(pid), None
        for ancestor in m.ancestors(snap, pid):
            home = proc_checkout(snap.procs[ancestor])
            if home is not None:
                owner = snap.procs[ancestor]
                break
        out.append((pid, owner, home))
    return out


# ---------------------------------------------------------------- finding the live install

@dataclass
class Found:
    """Every copy of the app on this PC that the run stops, and the one it migrates from."""

    candidates: list[Path]
    live: Path
    ports: list[int] = field(default_factory=list)


def _python_outside(folders: list[Path], report: Any) -> bool:
    for folder in folders:
        if m.inside(sys.executable, folder):
            report.add("guard", "refused", "python", f"this run's python lives in {folder.name}, which it would "
                       "rename; run setup.bat, which uses the system python")
            return False
    return True


def _ports(candidates: list[Path], mig: Any) -> list[int]:
    """The port each candidate's own config sets (the declared one where it sets none); declared with no candidate.

    The declared default is not asked on its own once a copy's config moves the app elsewhere: another program
    may own the default on this PC.
    """
    endpoint = mig.endpoint if mig is not None else None
    if endpoint is None:
        return []
    ports = {port for folder in candidates if (port := m.local_port(folder, endpoint)[0]) is not None}
    return sorted(ports or {endpoint.port})


def _holders(candidates: list[Path], ports: list[int], ref: Checkout, incoming: Path | None, snap: Any,
             report: Any, runner: Runner, why: dict[Path, str]) -> dict[Path, str] | None:
    """Which candidate holds which port, adding a same-app holder from elsewhere; None after a refusal."""
    held: dict[Path, str] = {}
    for port in ports:
        for pid, owner, home in port_holders(snap, port):
            exe = owner.exe if owner is not None and owner.exe else "an unknown program"
            if home is not None and incoming is not None and _same_folder(home, incoming):
                continue  # the incoming clone's guard refuses a running clone
            match = next((c for c in candidates if home is not None and _same_folder(home, c)), None)
            if match is None and home is not None:
                ok, reason = same_app(read_checkout(home, runner), ref)
                if ok:
                    candidates.append(home)
                    why[home] = reason
                    match = home
            if match is None:
                traced = f"started from the checkout {home}" if home is not None else "in no checkout"
                report.add("discover", "refused", f"port {port}", f"held by PID {pid} {exe} ({traced}), which is "
                           "not a copy of this app; nothing was stopped: free the port yourself, then run again")
                return None
            held.setdefault(match, f"holds port {port} (PID {pid})")
    return held


def discover(folder: Path, incoming: Path | None, ref: Checkout, mig: Any, snap: Any, report: Any,
             runner: Runner) -> Found | None:
    """Every copy of the app beside ``folder`` or on its port, and the live one; None after a refusal."""
    candidates: list[Path] = []
    why: dict[Path, str] = {}
    if folder.is_dir():
        if not _is_checkout_root(folder, runner):
            report.add("guard", "refused", folder.name, "not the root of a git checkout, so it cannot be shown to "
                       "be the same app; migrate from it with -MigrateFrom instead")
            return None
        ok, reason = same_app(read_checkout(folder, runner), ref)
        if ok:
            candidates.append(folder)
            why[folder] = reason
        else:
            report.add("discover", "other app", folder.name, f"left alone: {reason}; moved to a backup only when "
                       "the clone lands at its path")
    else:
        report.add("discover", "missing", folder.name, "no such folder: the clone lands there")
    for sibling in siblings(folder, incoming):
        ok, reason = same_app(read_checkout(sibling, runner), ref)
        if ok:
            candidates.append(sibling)
            why[sibling] = reason
        else:
            report.add("discover", "other app", sibling.name, f"left alone: {reason}")
    if snap is None:
        report.add("discover", m.UNKNOWN, "processes", "could not list processes and ports, so the live copy "
                   "cannot be told")
        return None
    ports = _ports(candidates, mig)
    held = _holders(candidates, ports, ref, incoming, snap, report, runner, why)
    if held is None:
        return None
    if not candidates:
        report.add("discover", "refused", folder.name, f"no copy of this app in {folder.parent}: nothing to "
                   "migrate from")
        return None
    mine = m.ancestors(snap, os.getpid())
    running = {c: f"PID {procs[0].pid} runs from it" for c in candidates
               if (procs := m.processes_from(snap, c, mine))}
    live = next(iter(held), None) or next(iter(running), None)
    if live is None:
        live = max(candidates, key=lambda c: (_commit_time(c, runner), c == folder))
        live_why = "nothing runs from any copy; the most recent commit"
    else:
        live_why = held.get(live) or running[live]
    for c in candidates:
        state = "live" if c == live else "stale"
        detail = live_why if c == live else held.get(c) or running.get(c) or "nothing runs from it"
        report.add("discover", state, c.name, f"{detail}; {why[c]}")
    return Found(candidates, live, ports)


# ---------------------------------------------------------------- guards

def guard_anchor(folder: Path, report: Any, runner: Runner) -> str | None:
    """``-InPlace``: the install's origin URL, or None when the folder cannot be cloned from."""
    if not _python_outside([folder], report):
        return None
    if not _is_checkout_root(folder, runner):
        report.add("guard", "refused", "folder", "not the root of a git checkout")
        return None
    url = m.git(["remote", "get-url", "origin"], folder, runner)
    if not url:
        report.add("guard", "refused", "folder", "has no origin remote to clone")
        return None
    report.add("guard", m.OK, "folder", str(folder))
    return str(url)


def guard_incoming(incoming: Path, folder: Path, report: Any, runner: Runner, snap: Any) -> bool:
    """Whether ``incoming`` is a fresh, unused clone beside the install; which app it is, discovery checks."""
    if not _python_outside([incoming], report):
        return False
    if m.inside(str(incoming), folder) or m.inside(str(folder), incoming):
        report.add("guard", "refused", "incoming clone", "is the install, or inside or around it")
        return False
    if not _is_checkout_root(incoming, runner):
        report.add("guard", "refused", "incoming clone", "not the root of a git checkout")
        return False
    status = m.git(["status", "--porcelain"], incoming, runner)
    if status is None:
        report.add("guard", m.UNKNOWN, "incoming clone", "git status did not run")
        return False
    if status:
        report.add("guard", "refused", "incoming clone", "not a fresh clone: it has changed or untracked files")
        return False
    if os.path.lexists(incoming / ".venv"):
        report.add("guard", "refused", "incoming clone", "already has a .venv, which names its current path; "
                   "clone it again and run nothing in it before -Replace")
        return False
    if snap is None:
        report.add("guard", m.UNKNOWN, "incoming clone", "could not list processes to show nothing runs from it")
        return False
    running = m.processes_from(snap, incoming, m.ancestors(snap, os.getpid()))
    if running:
        report.add("guard", "refused", "incoming clone", "something runs from it (PID "
                   + ", ".join(str(p.pid) for p in running) + "), so it cannot be renamed")
        return False
    report.add("guard", m.OK, "incoming clone", str(incoming))
    return True


def _guard_renamed(folders: list[Path], report: Any, runner: Runner) -> bool:
    """Each folder the run renames: not this run's python; its uncommitted edits reported as kept."""
    if not _python_outside(folders, report):
        return False
    for folder in folders:
        branch = m.git(["branch", "--show-current"], folder, runner)
        report.add("guard", m.OK, folder.name, f"on {branch or 'a detached HEAD'}; the clone takes the remote's "
                   "default branch")
        changes = m.git(["status", "--porcelain", "--untracked-files=no"], folder, runner)
        if changes:
            report.add("guard", "kept", f"{folder.name}: uncommitted changes",
                       f"{len(changes.splitlines())} tracked file(s), kept in its backup")
    return True


def _guard_other(other: Path, candidates: list[Path], report: Any, snap: Any) -> bool:
    """Whether another app's folder can be moved aside: this run stops only the app's own copies, so nothing
    else may run from it. Asked before anything stops, since its rename comes after the stop.
    """
    running = [p for p in m.processes_from(snap, other, m.ancestors(snap, os.getpid()))
               if not any(m.runs_from(p, c) for c in candidates)]
    if running:
        report.add("guard", "refused", other.name, "another app runs from it (PID "
                   + ", ".join(str(p.pid) for p in running) + "), so it cannot be renamed; nothing was stopped: "
                   "stop it yourself, then run again")
    return not running


@dataclass
class Declared:
    """The declaration a run goes by and where it was read."""

    mig: Any
    why: str  # why ``mig`` is None
    source: str
    unread: str = ""  # why the origin could not be read: what the real run will read is then not known
    note: str = ""  # the origin was read and gives no declaration, so the real run falls back too


def _parsed(read: Callable[[], dict[str, Any] | str]) -> tuple[Any, str]:
    """The migration declaration in what ``read`` returns, or (None, why)."""
    try:
        return m.parse_migration(read()), ""
    except ValueError as exc:
        return None, str(exc)


def _origin_declaration(anchor: Path, runner: Runner) -> Declared:
    """The declaration at the head of the origin's default branch, which a clone of ``anchor``'s origin checks out.

    The fetch adds objects and ``FETCH_HEAD`` under ``.git``; it moves no ref and no file. When the origin
    cannot be read, ``anchor``'s own file stands in.
    """
    source = "origin's default branch"
    if m.git(["fetch", "--quiet", "--no-tags", "--no-recurse-submodules", "origin", "HEAD"], anchor, runner) is None:
        unread = "git fetch failed"
    elif (text := m.git(["show", "FETCH_HEAD:.fleet.toml"], anchor, runner)) is None:
        unread = "no .fleet.toml could be read at its head"
    else:
        mig, why = _parsed(lambda: text)
        return Declared(mig, why, source)
    mig, why = _parsed(lambda: m.read_fleet_toml(anchor))
    return Declared(mig, why, source, unread=unread)


def _clone_declaration(reference: Path, incoming: Path, no_clone: bool, runner: Runner) -> Declared:
    """What ``-MigrateFrom`` will go by: the clone's declaration, asked of the origin when no clone is made."""
    if no_clone:
        return _origin_declaration(reference, runner)
    mig, why = _parsed(lambda: m.read_fleet_toml(reference))
    return Declared(mig, why, f"the fresh clone {incoming.name}")


def _live_declaration(declared: Declared, live: Path, candidates: list[Path]) -> Declared:
    """The first declaration that parses among this PC's copies, the live one first."""
    note = "" if declared.unread or declared.mig is not None else f"{declared.source} gives none ({declared.why})"
    first = ""
    for folder in (live, *candidates):
        mig, why = _parsed(lambda: m.read_fleet_toml(folder))
        if mig is not None:
            copy = "the live copy" if folder == live else "the copy"
            return Declared(mig, "", f"{copy} {folder.name}", declared.unread, note)
        first = first or why
    return Declared(None, first, f"the live copy {live.name}", declared.unread, note)


def _preview_copy(folder: Path, declared: Declared, report: Any) -> None:
    """Where the copy list comes from, then every file it would copy from the live copy, by name, never by value."""
    if declared.mig is None:
        origin = f"origin's default branch could not be read ({declared.unread}), and " if declared.unread else ""
        report.add("copy", m.UNKNOWN, ".fleet.toml", f"the copy list cannot be shown: {origin}{declared.why}")
        return
    detail = f"from {declared.source}"
    if declared.unread:
        detail += (f": origin's default branch could not be read ({declared.unread}), so the real run may copy "
                   "another list")
    elif declared.note:
        detail += f": {declared.note}"
    report.add("copy", m.UNKNOWN if declared.unread else m.OK, "copy list", detail)
    m.copy_declared(folder, None, declared.mig, report, old_running=False)  # the stop runs before the copy


def _preview_swap(folder: Path, incoming: Path, backup: Path | None, other: Path | None, source: Path,
                  report: Any) -> None:
    """The renames and the migration the swap would make."""
    if backup is not None:
        moved = "; another app, moved aside so the clone can land" if other is not None else ""
        report.add("in place", "would rename", folder.name, f"to {backup.name}, kept as a working checkout{moved}")
    report.add("in place", "would rename", incoming.name, f"to {folder.name}")
    report.add("in place", "would run", "setup.bat -MigrateFrom", f"{source.name}: copy (as listed), "
               "secrets, install, start, verify")


def _start_hint(folder: Path, mig: Any) -> str:
    start = " ".join(mig.start) if mig is not None else " ".join(m.DEFAULT_START)
    return f"start it again from {folder} with {start}"


# ---------------------------------------------------------------- clone, swap, migrate

def _run_child(folder: Path, source: Path, as_json: bool, runner: Runner) -> tuple[int, Any]:
    """The moved clone's ``setup.bat -MigrateFrom <source>``; with ``as_json``, its parsed report."""
    line = f'cmd.exe /d /s /c ""{folder / "setup.bat"}" -MigrateFrom "{source}"{" -Json" if as_json else ""}"'
    try:
        done = runner(line, cwd=folder, timeout=m.CLONE_TIMEOUT_S, check=False, stdin=subprocess.DEVNULL,
                      stdout=subprocess.PIPE if as_json else sys.stdout, stderr=sys.stderr,
                      encoding="utf-8" if as_json else None, errors="replace" if as_json else None)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, {"error": f"setup.bat -MigrateFrom did not run ({type(exc).__name__})"}
    if not as_json:
        return done.returncode, None
    try:
        return done.returncode, json.loads(done.stdout)
    except ValueError:
        return done.returncode, {"error": "setup.bat -MigrateFrom printed no JSON report"}


def _clone(url: str, incoming: Path, report: Any, runner: Runner) -> bool:
    """Clone beside the install before anything is stopped, so a failed clone changes nothing."""
    try:
        # conventions-exempt: a vendor-verbatim file cannot import the adopter's run_git; one clone
        done = runner(["git", "clone", "--", url, str(incoming)], cwd=incoming.parent, timeout=m.CLONE_TIMEOUT_S,
                      check=False, stdin=subprocess.DEVNULL, stdout=sys.stderr, stderr=sys.stderr)
        code = done.returncode
    except (OSError, subprocess.TimeoutExpired):
        code = -1
    if code == 0:
        report.add("in place", "cloned", url, f"into {incoming}")
        return True
    report.add("in place", m.FAILED, "clone", f"git clone exited {code}; the install was not touched"
               + (f"; remove the partial {incoming.name} before running again" if os.path.lexists(incoming) else ""))
    return False


def _swap(folder: Path, incoming: Path, backup: Path | None, live: Path, mig: Any, report: Any) -> bool:
    """Rename the install (when there is one) to its backup and the clone into its path; on a failure, undo."""
    if backup is not None:
        try:
            os.rename(folder, backup)
        except OSError as exc:
            report.add("in place", m.FAILED, "rename", f"{type(exc).__name__}: a terminal, editor or process still "
                       f"has a file or its current folder inside {folder.name}; nothing was renamed, "
                       f"{_start_hint(live, mig)}, close what holds it and run again")
            return False
        report.add("in place", "renamed", folder.name, f"to {backup.name}")
    try:
        os.rename(incoming, folder)
    except OSError as exc:
        why = f"{type(exc).__name__} renaming {incoming.name} to {folder.name}"
        if backup is None:
            report.add("in place", m.FAILED, "rename", f"{why}; nothing was renamed, {_start_hint(live, mig)}")
            return False
        try:
            os.rename(backup, folder)
        except OSError as back:
            report.add("in place", m.FAILED, "rename", f"{why}, and {type(back).__name__} renaming {backup.name} "
                       f"back: rename {backup.name} to {folder.name} by hand, then {_start_hint(live, mig)}")
            return False
        report.add("in place", m.FAILED, "rename", f"{why}; {backup.name} was renamed back, "
                   f"{_start_hint(live, mig)}; {incoming.name} is unchanged")
        return False
    report.add("in place", "renamed", incoming.name, f"to {folder.name}")
    return True


def _retire(live: Path, backup: Path, migrated: bool, mig: Any, report: Any) -> None:
    """After the migration, the live sibling becomes a backup; after a failed one it stays, the fallback."""
    if not migrated:
        report.add("in place", "kept", live.name, f"at its path, the fallback: {_start_hint(live, mig)}")
        return
    try:
        os.rename(live, backup)
    except OSError as exc:
        report.add("in place", m.FAILED, "rename", f"{type(exc).__name__} renaming {live.name} to {backup.name}: "
                   "the new copy serves; rename it by hand so nothing starts the old one again")
        return
    report.add("in place", "renamed", live.name, f"to {backup.name}, kept as a working checkout")


def _kept_clone(report: Any, made_here: bool, incoming: Path, folder: Path) -> Any:
    """The report, naming the clone this run made when it stops after making it."""
    if made_here:
        report.add("in place", "kept", incoming.name, f"a fresh clone: run {incoming.name}\\setup.bat -Replace "
                   f"{folder.name} to try again, or remove it")
    return report


def replace(folder: Path, incoming: Path | None, *, what_if: bool, as_json: bool, runner: Runner = subprocess.run,
            snapshot_fn: Callable[[], Any] | None = None, today: datetime.date | None = None,
            anchor: Path | None = None) -> Any:
    """Swap whichever copy of the app is live for the clone at ``incoming``, landing it at ``folder``.

    ``incoming`` None clones ``anchor``'s origin (``folder`` by default: ``-InPlace``) into
    ``<folder>.incoming`` first; under ``-WhatIf`` nothing is cloned, ``anchor``'s history stands in for the
    clone's and the declaration is read from its origin.
    """
    report = m.MigrationReport(mode="in place" if incoming is None else "replace", what_if=what_if)
    snapshot_fn = snapshot_fn or (lambda: m.take_snapshot(runner))
    folder = folder.resolve()
    if folder.parent.is_dir():
        os.chdir(folder.parent)  # a process whose current folder is inside it blocks the rename
    cloning = incoming is None
    incoming = (incoming or incoming_path(folder)).resolve()
    reference = incoming
    if cloning:
        anchor = (anchor or folder).resolve()
        url = guard_anchor(anchor, report, runner)
        if url is None:
            return report
        if os.path.lexists(incoming):
            report.add("guard", "refused", "incoming clone", f"{incoming.name} already exists: run {incoming.name}"
                       f"\\setup.bat -Replace {folder.name} if it is a fresh clone, or remove it")
            return report
        if what_if:
            report.add("in place", "would clone", url, f"into {incoming}")
            reference = anchor
        elif not _clone(url, incoming, report, runner):
            return report
    snap = snapshot_fn()
    have_clone = incoming.is_dir()
    if have_clone and not guard_incoming(incoming, folder, report, runner, snap):
        return report
    declared = _clone_declaration(reference, incoming, cloning and what_if, runner)
    found =discover(folder, incoming, read_checkout(reference, runner), declared.mig, snap, report, runner)
    if found is None:
        return _kept_clone(report, cloning and have_clone, incoming, folder)
    # An unread origin, or a clone too old to declare one beside a copy that does.
    if declared.unread or declared.mig is None:
        declared = _live_declaration(declared, found.live, found.candidates)
    mig = declared.mig
    # <root>/<app> that is no copy of the app: another app's repository, moved aside like an old install.
    other = folder if folder.is_dir() and not any(_same_folder(folder, c) for c in found.candidates) else None
    if other is not None and not _guard_other(other, found.candidates, report, snap):
        return _kept_clone(report, cloning and have_clone, incoming, folder)
    renamed = [c for c in (folder, found.live) if c.is_dir()]
    if not _guard_renamed(list(dict.fromkeys(renamed)), report, runner):
        return report
    m.inventory(found.live, report, runner, snap)
    m.stop_old(found.candidates, found.ports, report, runner, snapshot_fn, label="app copies")
    if report.failed:
        return _kept_clone(report, cloning and have_clone, incoming, folder)
    day = today or datetime.date.today()
    backup = backup_path(folder, day) if folder.is_dir() else None
    sibling = None if _same_folder(found.live, folder) else found.live
    source = sibling or backup or folder  # no sibling live means the install at ``folder`` is, so it has a backup
    if what_if:
        _preview_copy(found.live, declared, report)
        _preview_swap(folder, incoming, backup, other, source, report)
        if sibling is not None:
            report.add("in place", "would rename", sibling.name, f"to {backup_path(sibling, day).name} once the "
                       "migration passes, kept as a working checkout")
        return report
    if not _swap(folder, incoming, backup, found.live, mig, report):
        return report
    if not as_json:
        sys.stdout.write(report.render(final=False) + "\n\nMigrating from the live copy:\n")
        sys.stdout.flush()
        report.handed_off = True
    code, child = _run_child(folder, source, as_json, runner)
    report.migration = child
    kept = ""
    if backup is not None:
        kept = (f"; {backup.name} holds the other app that was at {folder.name}" if other is not None
                else f"; the backup {backup.name} stays as the fallback")
    report.add("in place", m.OK if code == 0 else m.FAILED, "setup.bat -MigrateFrom", f"exited {code}{kept}")
    if sibling is not None:
        _retire(sibling, backup_path(sibling, day), code == 0, mig, report)
    return report


def in_place(folder: Path, *, what_if: bool, as_json: bool, runner: Runner = subprocess.run,
             snapshot_fn: Callable[[], Any] | None = None, today: datetime.date | None = None) -> Any:
    """``-InPlace``: clone the install's origin beside it, then swap and migrate."""
    return replace(folder, None, what_if=what_if, as_json=as_json, runner=runner, snapshot_fn=snapshot_fn,
                   today=today)


def finish(report: Any, as_json: bool) -> int:
    """Print the report (only its last row when the child already printed its own); the exit code."""
    if report.handed_off:
        state, item, detail = report.rows["in place"][-1]
        sys.stdout.write(f"\nin place: [{state}] {item}  - {detail}\n")
        return 1 if report.failed else 0
    return int(m.emit(report, as_json))


def main(argv: list[str] | None = None) -> int:
    """``setup.bat -InPlace`` or ``setup.bat -Replace``, run from a temporary copy of this file."""
    parser = argparse.ArgumentParser(prog="setup.bat", description="Replace an install with a fresh clone at its "
                                     "canonical path, keeping every replaced folder as a backup.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("-InPlace", "--in-place", dest="in_place", action="store_true",
                      help="clone this install's origin beside it, then replace it")
    mode.add_argument("-Replace", "--replace", dest="replace", metavar="INSTALL",
                      help="replace INSTALL with the clone this setup.bat is in (setup.bat resolves it to -Folder)")
    parser.add_argument("-Folder", "--folder", dest="folder", required=True, help="the install's canonical path")
    parser.add_argument("-Incoming", "--incoming", dest="incoming", help="with -Replace: the clone to move in")
    parser.add_argument("-WhatIf", "--what-if", dest="what_if", action="store_true",
                        help="list every step and change nothing")
    parser.add_argument("-Json", "--json", dest="as_json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    if bool(args.replace) != bool(args.incoming):
        parser.error("-Incoming goes with -Replace, and -Replace needs it")
    incoming = Path(args.incoming) if args.incoming else None
    return finish(replace(Path(args.folder), incoming, what_if=args.what_if, as_json=args.as_json), args.as_json)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
