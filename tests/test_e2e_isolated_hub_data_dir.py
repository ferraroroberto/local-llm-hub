"""The e2e test hub keeps every runtime database out of the real data folder (#832).

``isolate_from_live_services`` redirected only the usage log, so the Machines
tab's diagnostics tests opened (and migrated) the owner's real
``diagnostics.db``. The fleet rule (project-scaffolding#404) is that a harness
sets the app's data-directory variable, because a per-file override covers only
the file it names.

Runs in a subprocess: the isolation stubs patch ``src`` modules in place and
``src.diagnostics.store`` resolves its path at import, so neither can be
checked in this process without leaking into the rest of the suite.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from src.runtime_data import app_dir_env_var

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_PROBE = """
import json, sys
from pathlib import Path
from tests.e2e._isolated_hub import isolate_from_live_services
isolate_from_live_services(Path(sys.argv[1]))
from src.diagnostics import store
print(json.dumps(str(store.DEFAULT_DB_PATH)))
"""


def test_isolated_hub_resolves_the_diagnostics_db_under_its_state_dir(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    real_dir = tmp_path / "real-data"
    stray_file = tmp_path / "stray" / "diagnostics.db"

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    # The caller's shell points at the "real" folder, both ways a harness can
    # leak: the app data directory and a per-file diagnostics override.
    env[app_dir_env_var("local-llm-hub")] = str(real_dir)
    env["LLM_HUB_DIAGNOSTICS_DB_PATH"] = str(stray_file)

    result = subprocess.run(
        [sys.executable, "-c", _PROBE, str(state_dir)],
        cwd=str(PROJECT_ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr

    resolved = Path(json.loads(result.stdout.strip().splitlines()[-1]))
    assert state_dir in resolved.parents, f"diagnostics.db resolves outside the e2e state dir: {resolved}"
    assert resolved.name == "diagnostics.db"
