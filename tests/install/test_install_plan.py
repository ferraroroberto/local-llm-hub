"""What `setup.bat` does for this app: the `[install]` table in `.fleet.toml`.

The installer itself is vendored and tested in project-scaffolding; these tests pin
this app's declaration: which configs it creates, which it must never create, and
that a top-up keeps what a PC already has.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from src import installer

REPO_ROOT = Path(__file__).resolve().parents[2]
WEBAPP_SAMPLE = "config/webapp_config.sample.json"
WEBAPP_TARGET = "config/webapp_config.json"
# Their samples hold placeholder vocabulary or placeholder peers, so a created copy would act as real data.
NEVER_CREATED = (
    "config/aliases.local.json",
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


def test_top_up_keeps_values_adds_missing_keys_and_backs_up(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    shutil.copyfile(REPO_ROOT / WEBAPP_SAMPLE, tmp_path / WEBAPP_SAMPLE)
    target = tmp_path / WEBAPP_TARGET
    target.write_text(json.dumps({"auth_token": "kept-value", "own_key": 1}), encoding="utf-8")
    plan = installer.load_plan(REPO_ROOT)

    report = installer.Report(what_if=False)
    installer.ensure_configs(tmp_path, plan, report, "20260102-030405")

    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["auth_token"] == "kept-value"
    assert data["own_key"] == 1
    assert data["cors_allow_origins"] == []
    assert (tmp_path / "config" / "webapp_config.json.bak-20260102-030405").is_file()

    again = installer.Report(what_if=False)
    installer.ensure_configs(tmp_path, plan, again, "20260102-030406")

    assert [status for status, _, _ in again.lines] == ["unchanged"]
    assert not (tmp_path / "config" / "webapp_config.json.bak-20260102-030406").exists()
