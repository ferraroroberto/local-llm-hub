"""What `setup.bat` does for this app: the `[install]` table in `.fleet.toml`.

The installer itself is vendored and tested in project-scaffolding; these tests pin
this app's declaration: which configs it creates, which it must never create, and
that a top-up keeps what a PC already has.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from src import installer, installer_declaration
from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[2]
WEBAPP_SAMPLE = "config/webapp_config.sample.json"
WEBAPP_TARGET = "config/webapp_config.json"
# Their samples hold placeholder vocabulary or placeholder peers, so a created copy would act as real data;
# the dictionary and the startup profile are read from their committed template until the admin saves them.
NEVER_CREATED = (
    "config/aliases.local.json",
    "config/transcription_glossary.json",
    "config/transcription_glossary.local.json",
    "config/machines.local.yaml",
    "config/cloudflared.yml",
    "config/startup_profile.json",
)


def test_only_the_webapp_config_is_created_from_a_sample() -> None:
    plan = installer.load_plan(REPO_ROOT)

    pairs = installer.config_pairs(REPO_ROOT, plan)

    assert pairs == [(WEBAPP_SAMPLE, WEBAPP_TARGET)]
    assert not set(NEVER_CREATED) & {target for _, target in pairs}
    assert WEBAPP_TARGET in plan.settings
    assert (REPO_ROOT / WEBAPP_SAMPLE).is_file()


def test_every_created_config_is_gitignored() -> None:
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert WEBAPP_TARGET in ignored
    assert "config/*.bak-*" in ignored


def _checkout_with_this_gitignore(root: Path) -> None:
    """A git checkout carrying this repo's .gitignore: the installer tops up only when git ignores the backup."""
    subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=60, creationflags=NO_WINDOW)
    shutil.copyfile(REPO_ROOT / ".gitignore", root / ".gitignore")


def test_top_up_keeps_values_adds_missing_keys_and_backs_up(tmp_path: Path) -> None:
    _checkout_with_this_gitignore(tmp_path)
    (tmp_path / "config").mkdir()
    shutil.copyfile(REPO_ROOT / WEBAPP_SAMPLE, tmp_path / WEBAPP_SAMPLE)
    target = tmp_path / WEBAPP_TARGET
    target.write_text(json.dumps({"auth_token": "kept-value", "own_key": 1}), encoding="utf-8")
    plan = installer.load_plan(REPO_ROOT)

    report = installer.Report(what_if=False)
    installer.ensure_configs(tmp_path, plan, report, "20260102-030405", subprocess.run)

    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["auth_token"] == "kept-value"
    assert data["own_key"] == 1
    assert data["cors_allow_origins"] == []
    assert (tmp_path / "config" / "webapp_config.json.bak-20260102-030405").is_file()

    again = installer.Report(what_if=False)
    installer.ensure_configs(tmp_path, plan, again, "20260102-030406", subprocess.run)

    assert [status for status, _, _ in again.lines] == ["unchanged"]
    assert not (tmp_path / "config" / "webapp_config.json.bak-20260102-030406").exists()


def _ignored(path: str) -> bool:
    done = subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO_ROOT, timeout=60,
                          check=False, creationflags=NO_WINDOW)
    return done.returncode == 0


def test_migration_carries_only_ignored_files_and_verifies_the_build_endpoint() -> None:
    mig = installer_declaration.parse_migration(installer_declaration.read_fleet_toml(REPO_ROOT))

    # An unignored copy would leave the clone dirty, and the next -MigrateFrom refuses a dirty clone.
    # A folder rule ("data/telemetry/") matches only a file below it on a checkout without the folder.
    assert not [item for item in mig.copy if not (_ignored(item) or _ignored(f"{item}/probe"))]
    assert not [name for name in mig.secrets if not any(name == c or name.startswith(f"{c}/") for c in mig.copy)]
    assert not [item for item in mig.copy if item.endswith((".db", ".sqlite", ".sqlite3", "-wal", "-shm"))]
    assert mig.shared == ["local-llm-hub"]
    assert mig.endpoint is not None
    assert (mig.endpoint.port, mig.endpoint.version_path) == (8000, "/admin/api/version")
