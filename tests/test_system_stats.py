"""Unit tests for the machine-card probes in src/system_stats.py."""

from __future__ import annotations


def test_disk_stats_probes_the_drive_the_hub_lives_on(monkeypatch):
    """The Disk gauge reports the checkout's own drive, not a hardcoded system drive (#710)."""
    from types import SimpleNamespace

    from src import system_stats

    seen = []

    def fake_usage(root):
        seen.append(root)
        return SimpleNamespace(used=1024 ** 3, total=4 * 1024 ** 3, percent=25.0)

    monkeypatch.setattr(system_stats.psutil, "disk_usage", fake_usage)
    out = system_stats.disk_stats()
    assert seen == [system_stats._PROJECT_ROOT.anchor]
    assert out == {"used_gb": 1.0, "total_gb": 4.0, "percent": 25.0}
