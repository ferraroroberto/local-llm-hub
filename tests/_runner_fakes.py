"""Fakes for the Actions-runner tests: the SSH layer and the GitHub layer.

``FakeMac`` stands in for ``ssh`` to the runner Mac (it prints what
``svc.sh``, ``defaults``, ``fdesetup`` and ``pmset`` print, and obeys
``svc.sh start|stop|install``); ``FakeGitHub`` for ``gh api`` (the runners
list and the latest workflow run). Every value is invented, so no real
machine, repo or runner is ever touched. Shared by the router tests and the
Machines-card e2e, which drive ``src.fleet.actions_runner`` through them.
"""

from __future__ import annotations

from src.fleet import actions_runner as ar
from src.fleet import remote_stats
from src.fleet.ssh_exec import SshResult

REPO = "example-owner/example-repo"
NAME = "example-runner"
USER = "youruser"

CONFIG = {
    "hub": {"port": 8000},
    "hosts": {
        "hubbox": {"platform": "linux", "default": True, "enabled": ["m1"]},
        "mini": {"platform": "darwin", "enabled": [], "display_name": "Mini"},
        "plain": {"platform": "linux", "enabled": []},
    },
    "models": {
        "m1": {
            "display_name": "m1", "backend": "openai", "engine": "llama-server",
            "port": 8081, "model_path": "models/m1.gguf", "hosts": ["hubbox"], "startup": "eager",
        },
    },
}
OVERLAY = {
    "hosts": {
        "hubbox": {"address": "192.0.2.1", "ssh_user": USER, "actions_runner": {}},
        "mini": {
            "address": "192.0.2.10", "ssh_user": USER,
            "actions_runner": {"workflow": "nightly.yml"},
        },
        "plain": {"address": "192.0.2.11", "ssh_user": USER},
    },
}


class FakeMac:
    """The runner Mac as ssh sees it."""

    def __init__(self) -> None:
        self.service = "running"  # running | stopped | loaded | not_installed
        self.autologin = USER  # a user name, or None for "not set"
        self.filevault = "Off"
        self.sleep = 0
        self.commands: list[str] = []
        self.reachable = True
        self.dir_missing = False
        self.on_verb = lambda verb: None

    def _svc(self) -> str:
        if self.service == "not_installed":
            return "\nnot installed\n\nrc"
        if self.service == "running":
            return "status actions.runner.x:\n\n/p.plist\n\nStarted:\n4242\t0\tactions.runner.x.y\n"
        if self.service == "loaded":
            return "status actions.runner.x:\n\n/p.plist\n\nStarted:\n-\t1\tactions.runner.x.y\n"
        return "status actions.runner.x:\n\n/p.plist\n\nStopped\n"

    def status_text(self) -> str:
        if self.dir_missing:
            return "@@NODIR\n"
        runner_json = (
            '\ufeff{"agentName": "%s", "gitHubUrl": "https://github.com/%s"}' % (NAME, REPO)
        )
        auto = self.autologin or (
            "The domain/default pair of (/Library/Preferences/com.apple.loginwindow, autoLoginUser) does not exist"
        )
        return (
            f"@@RUNNER\n{runner_json}\n\n@@SVC\n{self._svc()}\n@@SVCRC {1 if self.service == 'not_installed' else 0}\n"
            f"@@AUTOLOGIN\n{auto}\n@@FILEVAULT\nFileVault is {self.filevault}.\n"
            f"@@PMSET\n autorestart          1\n sleep                {self.sleep}\n"
        )

    def run_ssh(self, target, command, *, key_path=None, timeout, connect_timeout=5):
        self.commands.append(command)
        if not self.reachable:
            return SshResult(None, "", "", error="TimeoutExpired: fake")
        for verb in ("install", "start", "stop"):
            if f"./svc.sh {verb}" in command:
                self.on_verb(verb)
                return SshResult(0, "ok\n", "")
        return SshResult(0, self.status_text(), "")


class FakeGitHub:
    """``gh api`` for the runners list and the latest workflow run."""

    def __init__(self) -> None:
        self.status = "online"
        self.busy = False
        self.registered = True
        self.error: str | None = None
        self.run_conclusion = "success"
        self.reads_until_flip = 0  # reads that still show the old state after a verb
        self._pending: str | None = None
        self.paths: list[str] = []

    def flip_to(self, status: str) -> None:
        self._pending = status

    def api(self, path):
        self.paths.append(path)
        if self.error:
            return None, self.error
        if path.startswith(f"repos/{REPO}/actions/runners"):
            if self._pending and self.reads_until_flip <= 0:
                self.status, self._pending = self._pending, None
            elif self._pending:
                self.reads_until_flip -= 1
            runners = [{"name": NAME, "status": self.status, "busy": self.busy}] if self.registered else []
            return {"runners": runners}, None
        if path.startswith(f"repos/{REPO}/actions/workflows/nightly.yml/runs"):
            return {"workflow_runs": [{
                "status": "completed", "conclusion": self.run_conclusion,
                "updated_at": "2026-11-07T09:53:18Z", "created_at": "2026-11-07T08:36:12Z",
            }]}, None
        return None, "GitHub refused the request (HTTP 404)."


def install_fakes(monkeypatch):
    """Point ``actions_runner`` at a fresh ``FakeMac`` and ``FakeGitHub`` (with
    a verb that flips both the way the real service does) and return them."""
    mac, gh = FakeMac(), FakeGitHub()
    monkeypatch.setattr(ar, "run_ssh", mac.run_ssh)
    monkeypatch.setattr(ar, "_gh_api", gh.api)
    monkeypatch.setattr(remote_stats, "dial_address", lambda host, wait=False: host.address)

    async def _dial(host):
        return host.address

    monkeypatch.setattr(remote_stats, "dial_address_async", _dial)
    monkeypatch.setattr(ar, "_CONVERGE_DEADLINE_S", 0.4)
    monkeypatch.setattr(ar, "_CONVERGE_INTERVAL_S", 0.02)
    ar._gh_cache.clear()
    ar._locks.clear()

    def verb(name):
        # The launch service flips at once; GitHub follows once the listener
        # connects or drops.
        mac.service = "stopped" if name == "stop" else "running"
        gh.flip_to("offline" if name == "stop" else "online")

    mac.on_verb = verb
    return mac, gh
